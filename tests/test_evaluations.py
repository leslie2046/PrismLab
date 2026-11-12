"""M0 invariants: immutable evidence, missing data, serialized worker states."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch

import psycopg
from fastapi import HTTPException

from backend.evaluation_contracts import dependency_check, generation_engine, metric_catalog, safe_evidence
from backend.evaluation_routes import install_evaluation_routes
from backend.evaluation_store import EvaluationStore
from tests import test_datasets as fixtures


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        fixtures.DatasetApiTests.setUp(self)
        install_evaluation_routes(self.app, self.store, self.resolve, lambda: self.store)
        self.evaluations = EvaluationStore(self.store)
        self.dataset_id = fixtures.DatasetApiTests.create(self)
        self.repo.add(self.dataset_id, [{'input': 'Question', 'expected_output': 'Reference',
            'context': ['Original source'], 'actual_output': 'Old unrelated output',
            'retrieval_context': ['Old retrieval']}])
        self.repo.save_sources(self.dataset_id, [{'id': 'source', 'content': 'Frozen source', 'metadata': {'api_key': 'source-secret'}}])
        self.config.update(provider='vllm', base_url='https://user:pass@fixture.test/v1?token=url-secret',
                           api_key='config-secret', api_key_encrypted='cipher-secret', credentials=[{'api_key': 'nested-secret'}])
        self.request = {'dataset_id': self.dataset_id, 'target': {'benchmark': 'llm', 'selection': self.selection,
                        'prompt': 'Target instructions'}, 'judge': self.selection,
                        'metrics': [{'id': 'deepeval.answer_relevancy'}, {'id': 'deepeval.reference_correctness'}],
                        'idempotency_key': 'test-request'}

    def test_removed_http_targets_rejected_before_model_resolution(self):
        self.resolve.reset_mock()
        for changes in (
            {'target': {**self.request['target'], 'benchmark': 'http'}},
            {'sample_kinds': ['agent']},
            {'evaluation_type': 'retrieval', 'template': 'retrieval',
             'target': {**self.request['target'], 'benchmark': 'http'}},
        ):
            with self.subTest(changes=changes):
                response = self.client.post('/api/evaluation-runs', json={**self.request, **changes})
                self.assertEqual(response.status_code, 422, response.text)
        self.resolve.assert_not_called()
        self.assertEqual(self.evaluations.list(), [])

    def test_new_workspace_omits_removed_integration_tables(self):
        with self.store._session() as connection:
            for name in ('automation_tokens', 'evaluation_connectors'):
                self.assertIsNone(connection.execute('SELECT to_regclass(%s) AS relation', (name,)).fetchone()['relation'])

    def test_history_scopes_by_immutable_dataset_version_before_limit(self):
        original = self.prepare()
        other_dataset = fixtures.DatasetApiTests.create(self)
        self.repo.add(other_dataset, [{'input': 'Another question', 'expected_output': 'Answer', 'context': ['Source']}])
        other = self.prepare({**self.request, 'dataset_id': other_dataset, 'idempotency_key': 'other-history'})
        response = self.client.get('/api/evaluation-runs', params={'dataset_id': self.dataset_id, 'limit': 1})
        self.assertEqual(response.status_code, 200)
        rows = response.json()['items']
        self.assertEqual([item['id'] for item in rows], [original['id']])
        self.assertEqual(rows[0]['dataset_id'], self.dataset_id)
        self.assertEqual(rows[0]['dataset_version_id'], original['evidence']['dataset_version_id'])
        self.assertEqual(rows[0]['dataset_name'], self.evaluations.version(original['evidence']['dataset_version_id'])['dataset']['name'])
        self.assertNotIn('worker_token', rows[0])
        self.assertEqual([item['id'] for item in self.evaluations.list(dataset_id=other_dataset)], [other['id']])
        self.assertEqual(self.client.get('/api/evaluation-runs', params={'dataset_id': 'missing'}).json(), {'items': []})
        self.assertEqual(len(self.evaluations.list()), 2)
        # Removing source data must not orphan the history or its frozen evidence.
        self.repo.delete(self.dataset_id)
        self.assertEqual(self.evaluations.list(dataset_id=self.dataset_id)[0]['id'], original['id'])

    def prepare(self, value=None):
        response = self.client.post('/api/evaluation-runs', json=value or self.request)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_delete_cleans_results_and_preserves_shared_data(self):
        run = self.prepare()
        other = self.prepare({**self.request, 'idempotency_key': 'other'})
        version = self.evaluations.version(run['evidence']['dataset_version_id'])
        self.evaluations.cache_put('shared', {'score': .9})
        path = '/api/evaluation-runs/' + run['id']
        self.assertEqual(self.client.delete(path).json(), {'deleted': True})
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.delete(path).status_code, 404)
        self.assertEqual(self.evaluations.version(version['id']), version)
        self.assertEqual(self.client.get('/api/datasets/' + self.dataset_id).status_code, 200)
        self.assertEqual(self.client.get('/api/evaluation-runs/' + other['id']).status_code, 200)
        self.assertEqual(self.evaluations.cache_get('shared'), {'score': .9})
        with self.store._session() as c:
            for table in ('evaluation_cases', 'metric_results'):
                self.assertEqual(c.execute(f'SELECT count(*) AS n FROM {table} WHERE run_id=%s', (run['id'],)).fetchone()['n'], 0)
                self.assertGreater(c.execute(f'SELECT count(*) AS n FROM {table} WHERE run_id=%s', (other['id'],)).fetchone()['n'], 0)

    def test_delete_rejects_active_and_fences_cancelled_workers(self):
        run = self.prepare()
        path = '/api/evaluation-runs/' + run['id']
        self.evaluations.enqueue(run['id'])
        self.assertEqual(self.client.delete(path).status_code, 409)
        claim = self.evaluations.claim('delete-test')
        self.assertEqual(self.client.delete(path).status_code, 409)
        self.evaluations.cancel(run['id'])
        self.assertEqual(self.client.delete(path).status_code, 200)
        with self.assertRaises(HTTPException) as error:
            self.evaluations.record_target(run['id'], run['cases'][0]['id'], claim['worker_token'], observation={'actual_output': 'late'})
        self.assertEqual(error.exception.status_code, 404)

    def test_delete_terminal_states(self):
        for status in ('completed', 'partial_failed', 'failed', 'cancelled'):
            run = self.prepare({**self.request, 'idempotency_key': status})
            with self.store._session() as c:
                payload = json.loads(self.evaluations._row(c, run['id'], lock=True)['payload'])
                payload.update(status=status, queue_state='finished')
                self.evaluations._save_run(c, payload)
            self.assertEqual(self.client.delete('/api/evaluation-runs/' + run['id']).status_code, 200)

    def test_delete_and_enqueue_serialize(self):
        run = self.prepare()
        def enqueue():
            try:
                self.evaluations.enqueue(run['id'])
            except HTTPException as error:
                self.assertEqual(error.status_code, 404)
        def delete():
            try:
                self.evaluations.delete(run['id'])
            except HTTPException as error:
                self.assertEqual(error.status_code, 409)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(enqueue), pool.submit(delete)]
            for future in futures:
                future.result()
        remaining = self.client.get('/api/evaluation-runs/' + run['id'])
        self.assertIn(remaining.status_code, (200, 404))
        if remaining.status_code == 200:
            self.assertEqual(remaining.json()['queue_state'], 'queued')
            self.assertTrue(remaining.json()['cases'])

    def test_evidence_survives_all_live_edits_and_deletion(self):
        self.store.save_system_settings({'evaluation_judge_prompt': 'Original judge instructions'})
        run = self.prepare()
        version_id = run['evidence']['dataset_version_id']
        version = self.evaluations.version(version_id)
        sample = version['samples'][0]
        self.assertEqual(sample['expected_output'], 'Reference')
        self.assertIsNone(run['cases'][0]['observation'])
        self.assertIn('actual_output', run['metric_results'][0]['dependency']['awaiting_fields'])
        self.repo.update_item(self.dataset_id, sample['id'], {'input': 'Changed', 'expected_output': 'Changed'})
        self.repo.save_sources(self.dataset_id, [{'content': 'Changed source'}])
        self.store.save_system_settings({'evaluation_judge_prompt': 'Changed instructions'})
        self.config['model'] = 'changed-model'
        self.repo.delete(self.dataset_id)
        self.assertEqual(self.evaluations.version(version_id), version)
        current=self.client.get('/api/evaluation-runs/' + run['id']).json()
        current.pop('summary',None)
        self.assertEqual(current,run)
        self.assertEqual(run['evidence']['result_judge']['prompt'], 'Original judge instructions')
        self.assertEqual(run['evidence']['target']['model'], 'test-model')
        text = json.dumps([run, version])
        for secret in ('source-secret', 'config-secret', 'cipher-secret', 'nested-secret', 'url-secret', 'user:pass'):
            self.assertNotIn(secret, text)
        self.assertEqual(run['evidence']['target']['base_url'], 'https://fixture.test/v1')
        with self.assertRaises(psycopg.errors.RaiseException), self.store._session() as connection:
            connection.execute('UPDATE dataset_versions SET payload=%s WHERE id=%s', ('{}', version_id))
        with self.assertRaises(psycopg.errors.RaiseException), self.store._session() as connection:
            connection.execute('UPDATE evaluation_runs SET evidence=%s WHERE id=%s', ('{}', run['id']))

    def test_parallel_idempotency_and_changed_request_conflict(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            runs = list(pool.map(lambda _: self.client.post('/api/evaluation-runs', json=self.request), range(5)))
        self.assertEqual({r.status_code for r in runs}, {201})
        self.assertEqual(len({r.json()['id'] for r in runs}), 1)
        self.assertEqual(len(self.evaluations.list()), 1)
        changed = deepcopy(self.request)
        changed['target']['prompt'] = 'Different'
        self.assertEqual(self.client.post('/api/evaluation-runs', json=changed).status_code, 409)
        self.resolve.side_effect = HTTPException(400, 'deleted model')
        self.assertEqual(self.prepare()['id'], runs[0].json()['id'])

    def test_registry_and_reference_retrieval_dependencies(self):
        catalog = {m['id']: m for m in metric_catalog()}
        correctness = catalog['deepeval.reference_correctness']
        check = dependency_check(correctness, {'input': 'q'})
        self.assertEqual(check['missing_fields'], ['expected_output'])
        relevance = catalog['deepeval.answer_relevancy']
        self.assertEqual(dependency_check(relevance, {'input': 'q'}, {'actual_output': 'a'})['data_status'], 'ready')
        faithful = catalog['deepeval.faithfulness']
        check = dependency_check(faithful, {'input': 'q', 'context': ['reference']}, {'actual_output': 'a', 'retrieval_context': []})
        self.assertEqual(check['missing_fields'], ['retrieval_context'])
        self.assertEqual(dependency_check(faithful, {}, {'actual_output': 'a', 'retrieval_context': ['actual']})['data_status'], 'ready')
        for metric in catalog.values():
            for key in ('implementation', 'implementation_version', 'contract_version', 'required_fields', 'score_range', 'threshold', 'model_dependencies', 'usage'):
                self.assertIn(key, metric)
            self.assertTrue(metric['executor_available'])
        self.assertIsNone(generation_engine('ragas')['sdk_version'])
        self.assertFalse(generation_engine('ragas')['official_sdk'])
        source = safe_evidence({'dify_config_id': 'connection', 'dataset_id': 'external-kb'})
        self.assertEqual(source['external_dify_dataset_id'], 'external-kb')
        self.assertEqual(safe_evidence({'input': {'api_key': 'hidden'}}), {'input': {}})
        self.assertEqual(safe_evidence({'input': 'https://example.test/?q=question'})['input'], 'https://example.test/?q=question')

    def test_missing_reference_is_null_and_not_retryable(self):
        item = self.repo.items(self.dataset_id)['items'][0]
        self.repo.update_item(self.dataset_id, item['id'], {'input': 'q', 'context': ['original']})
        request = deepcopy(self.request)
        request['metrics'] = [{'id': 'deepeval.reference_correctness'}, {'id': 'deepeval.faithfulness'}]
        run = self.prepare(request)
        token = self.evaluations.start(run['id'])
        self.evaluations.record_target(run['id'], run['cases'][0]['id'], token, observation={'actual_output': 'a'})
        value = self.evaluations.get(run['id'])
        self.assertEqual(value['status'], 'failed')
        self.assertTrue(all(m['score'] is None and m['passed'] is None and m['status'] == 'missing_data' for m in value['metric_results']))
        self.assertEqual(self.client.post(f"/api/evaluation-runs/{run['id']}/retry-failed").status_code, 409)

    def test_target_and_judge_errors_retry_preserves_completed_and_history(self):
        run = self.prepare()
        token = self.evaluations.start(run['id'])
        case = run['cases'][0]
        self.evaluations.record_target(run['id'], case['id'], token, error=True)
        failed = self.evaluations.get(run['id'])
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['cases'][0]['error'], 'target_call_failed')
        prepared = self.evaluations.retry_failed(run['id'])
        self.assertEqual(prepared['cases'][0]['history'][0]['status'], 'target_error')
        new_token = self.evaluations.start(run['id'])
        with self.assertRaises(HTTPException):
            self.evaluations.record_target(run['id'], case['id'], token, observation={'actual_output': 'stale'})
        self.evaluations.record_target(run['id'], case['id'], new_token, observation={'actual_output': 'New answer'})
        one, two = run['metric_results']
        self.evaluations.record_metric(run['id'], one['id'], new_token, score=0.5, reason='At threshold')
        self.evaluations.record_metric(run['id'], two['id'], new_token, error=True)
        value = self.evaluations.get(run['id'])
        self.assertEqual(value['status'], 'partial_failed')
        self.assertTrue(next(m for m in value['metric_results'] if m['id'] == one['id'])['passed'])
        self.evaluations.retry_failed(run['id'])
        token3 = self.evaluations.start(run['id'])
        self.evaluations.record_metric(run['id'], two['id'], token3, score=0.8, reason='Correct')
        value = self.evaluations.get(run['id'])
        self.assertEqual(value['status'], 'completed')
        self.assertEqual(value['attempt'], 3)
        self.assertEqual(next(m for m in value['metric_results'] if m['id'] == one['id'])['score'], 0.5)
        self.assertEqual(next(m for m in value['metric_results'] if m['id'] == two['id'])['history'][-1]['error'], 'judge_call_failed')
        self.assertEqual(value['evidence'], run['evidence'])

    def test_cancellation_rejects_late_writes_and_duplicate_submission(self):
        run = self.prepare()
        token = self.evaluations.start(run['id'])
        case = run['cases'][0]
        self.evaluations.record_target(run['id'], case['id'], token, observation={'actual_output': 'a'})
        with self.assertRaises(HTTPException):
            self.evaluations.record_target(run['id'], case['id'], token, observation={'actual_output': 'duplicate'})
        response = self.client.post(f"/api/evaluation-runs/{run['id']}/cancel")
        self.assertEqual(response.json()['status'], 'cancelled')
        self.assertNotIn('worker_token', response.json())
        with self.assertRaises(HTTPException):
            self.evaluations.record_metric(run['id'], run['metric_results'][0]['id'], token, score=1)
        self.assertEqual(self.evaluations.get(run['id'])['cases'][0]['observation']['actual_output'], 'a')
        self.assertEqual(self.client.post(f"/api/evaluation-runs/{run['id']}/retry-failed").status_code, 409)

    def test_restart_recovery_rejects_worker_and_preserves_evidence(self):
        run = self.prepare()
        token = self.evaluations.start(run['id'])
        self.store.initialize()
        value = self.evaluations.get(run['id'])
        self.assertEqual(value['status'], 'failed')
        self.assertEqual(value['error'], 'server_restarted')
        self.assertEqual(value['evidence'], run['evidence'])
        self.assertEqual(value['cases'][0]['status'], 'target_error')
        self.evaluations.retry_failed(run['id'])
        self.evaluations.start(run['id'])
        with self.assertRaises(HTTPException):
            self.evaluations.record_target(run['id'], run['cases'][0]['id'], token, error=True)

    def test_preparation_never_calls_models_and_rejects_bad_config(self):
        with patch('shared.llm_stream.stream_chat', side_effect=AssertionError('M0 must not call models')):
            run = self.prepare()
        self.assertEqual(run['status'], 'prepared')
        self.assertTrue(run['execution_available'])
        self.assertNotIn('worker_token', run)
        self.assertTrue(self.client.get('/api/evaluation-capabilities').json()['execution_available'])
        for field, value in [('metrics', [{'id': 'unknown'}]), ('metrics', [{'id': 'ragas.faithfulness'}]), ('scoring_engine', 'other')]:
            request = {**self.request, field: value, 'idempotency_key': 'invalid-' + field}
            self.assertIn(self.client.post('/api/evaluation-runs', json=request).status_code, (400, 422))
        for score in (-0.1, 1.1, float('nan'), True, None):
            # Internal worker score contract also rejects invalid/non-finite data.
            if score is None:
                continue
            with self.subTest(score=score):
                token = self.evaluations.start(run['id']) if score == -0.1 else token
                if score == -0.1:
                    self.evaluations.record_target(run['id'], run['cases'][0]['id'], token, observation={'actual_output': 'a'})
                with self.assertRaises(ValueError):
                    self.evaluations.record_metric(run['id'], run['metric_results'][0]['id'], token, score=score)

    def test_independent_defaults_old_clients_and_reset(self):
        self.store.save_system_settings({'dataset_default_llm': self.selection, 'dataset_prompt': 'Original generation'})
        response = self.client.put('/api/dataset-settings', json={'default_judge_llm': self.selection,
            'scoring_engine': 'ragas', 'judge_prompt': 'Judge actual answers carefully'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['scoring_engine'], 'ragas')
        self.assertEqual(response.json()['prompt'], 'Original generation')
        self.assertEqual(response.json()['default_llm'], self.selection)
        old = self.client.put('/api/dataset-settings', json={'prompt': 'Generation only'}).json()
        self.assertEqual(old['judge_prompt'], 'Judge actual answers carefully')
        self.assertEqual(old['default_judge_llm']['model'], 'test-model')
        self.assertIsNone(old['default_critic_llm'])
        self.assertEqual(self.client.put('/api/dataset-settings', json={'judge_prompt': ' '}).status_code, 422)
        reset = self.client.put('/api/dataset-settings', json={'judge_prompt': None, 'default_judge_llm': None}).json()
        self.assertEqual(reset['judge_prompt'], reset['default_judge_prompt'])
        self.assertIsNone(reset['default_judge_llm'])

    def test_duplicate_metric_and_snapshot_edit_are_serialized(self):
        run = self.prepare()
        token = self.evaluations.start(run['id'])
        self.evaluations.record_target(run['id'], run['cases'][0]['id'], token, observation={'actual_output': 'answer'})
        def submit(score):
            try:
                self.evaluations.record_metric(run['id'], run['metric_results'][0]['id'], token, score=score)
                return 'saved'
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, [0.2, 0.8]))
        self.assertCountEqual(outcomes, ['saved', 409])
        sample = self.repo.items(self.dataset_id)['items'][0]
        request = {**self.request, 'idempotency_key': 'snapshot-edit-race'}
        with ThreadPoolExecutor(max_workers=2) as pool:
            version_job = pool.submit(self.prepare, request)
            edit_job = pool.submit(self.repo.update_item, self.dataset_id, sample['id'], {'input': 'Changed atomically'})
            version_run = version_job.result()
            edit_job.result()
        frozen = self.evaluations.version(version_run['evidence']['dataset_version_id'])['samples'][0]
        self.assertIn(frozen['input'], ['Question', 'Changed atomically'])
        if frozen['input'] == 'Changed atomically':
            self.assertIsNone(frozen['expected_output'])
        else:
            self.assertEqual(frozen['expected_output'], 'Reference')

    def test_local_and_external_ids_are_distinct(self):
        self.config.update(dataset_id='external-knowledge-base', app_id='external-app')
        self.request['target']['benchmark'] = 'dify-retrieve'
        run = self.prepare()
        self.assertEqual(run['evidence']['target']['external_dify_dataset_id'], 'external-knowledge-base')
        version = self.evaluations.version(run['evidence']['dataset_version_id'])
        self.assertEqual(version['local_dataset_id'], self.dataset_id)
        self.assertNotEqual(version['local_dataset_id'], run['evidence']['target']['external_dify_dataset_id'])
