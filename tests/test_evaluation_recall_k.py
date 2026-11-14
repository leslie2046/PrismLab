"""Dify's configured cutoff is frozen separately from actual retrieval count."""
import json
import unittest
from unittest.mock import MagicMock, patch

import requests

from backend.dataset_annotations import item_hash
from backend.evaluation_recall import read_dify_top_k
from backend.evaluation_execution import score_rule
from tests import test_evaluations as fixtures
from tests import test_evaluation_execution as execution
from tests import test_evaluation_retrieval as retrieval


def settings(k=10, field='retrieval_model_dict'):
    response = MagicMock(status_code=200, content=b'{}')
    response.json.return_value = {field: {'top_k': k}}
    return response


class RecallKTests(unittest.TestCase):
    execute = execution.ExecutionTests.execute

    def setUp(self):
        fixtures.EvaluationTests.setUp(self)
        self.config['dataset_id'] = 'external kb/1'
        self.request.update(evaluation_type='retrieval', template='retrieval', scoring_engine='rules', judge=None,
                            metrics=[{'id':'rules.recall_at_k', 'k':5}])
        self.request['target'].update(benchmark='dify-retrieve')
        item = self.repo.items(self.dataset_id)['items'][0]
        annotation = {'content_hash':item_hash(item), 'review_status':'approved', 'relevant_chunk_ids':['chunk-6']}
        with self.store._session() as c:
            c.execute('INSERT INTO dataset_annotations VALUES (%s,%s,%s)', (item['id'],self.dataset_id,json.dumps(annotation)))

    def prepare(self):
        response = self.client.post('/api/evaluation-runs', json=self.request)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_follows_configured_ten_with_seven_returned_and_query_only(self):
        with patch('backend.evaluation_recall.requests.get', return_value=settings(10)) as get:
            prepared = self.prepare()
            # Retried creation with the same idempotency key must reuse the frozen K.
            same = self.prepare()
        self.assertEqual(same['id'], prepared['id'])
        get.assert_called_once()
        self.assertTrue(get.call_args.args[0].endswith('/datasets/external%20kb%2F1'))
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        metric = prepared['evidence']['metrics'][0]
        self.assertEqual((metric['k'], metric['k_source']), (10, 'dify'))
        response = retrieval.reply({'records':[
            {'segment':{'id':f'chunk-{i}', 'content':f'Context {i}'}} for i in range(1, 8)]})
        with patch('backend.evaluation_execution.requests.get') as get, \
             patch('backend.evaluation_execution.requests.post', return_value=response) as post:
            run = self.execute(prepared)
        get.assert_not_called()
        self.assertEqual(post.call_args.kwargs['json'], {'query':'Question'})
        self.assertEqual(run['status'], 'completed', run)
        self.assertEqual(run['metric_results'][0]['score'], 1)
        self.assertIn('Recall@10', run['metric_results'][0]['reason'])
        self.assertIn('returned 7 chunks', run['metric_results'][0]['reason'])
        listed = self.client.get('/api/evaluation-runs').json()['items'][0]
        self.assertEqual(listed['summary']['metrics']['rules.recall_at_k']['k'], 10)

    def test_rerun_refreshes_dify_k_without_modifying_history(self):
        with patch('backend.evaluation_recall.requests.get', return_value=settings(10)):
            original = self.prepare()
        with patch('backend.evaluation_recall.requests.get', return_value=settings(3)) as get:
            response = self.client.post(f'/api/evaluation-runs/{original["id"]}/rerun')
        self.assertEqual(response.status_code, 201, response.text)
        current = response.json()
        self.assertEqual(current['evidence']['metrics'][0]['k'], 3)
        self.assertEqual(current['evidence']['dataset_version_id'], original['evidence']['dataset_version_id'])
        self.assertEqual(self.evaluations.get(original['id'])['evidence'], original['evidence'])
        get.assert_called_once()

    def test_missing_or_unreadable_k_never_falls_back_to_five(self):
        for value in (None, 0, -1, True, '10'):
            with self.subTest(value=value), patch('backend.evaluation_recall.requests.get', return_value=settings(value)):
                response = self.client.post('/api/evaluation-runs', json=self.request)
            self.assertEqual(response.status_code, 502, response.text)
            self.assertIn('Top K', response.json()['detail'])
            self.assertEqual(self.evaluations.list(), [])
        response = settings()
        response.status_code = 403
        with patch('backend.evaluation_recall.requests.get', return_value=response):
            self.assertEqual(self.client.post('/api/evaluation-runs', json=self.request).status_code, 502)
        with patch('backend.evaluation_recall.requests.get', side_effect=requests.Timeout('private-key private-url')):
            response = self.client.post('/api/evaluation-runs', json=self.request)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private-key', response.text)


class MultiRecallKTests(unittest.TestCase):
    setUp = retrieval.MultiRetrievalTests.setUp
    prepare = fixtures.EvaluationTests.prepare
    execute = execution.ExecutionTests.execute
    post = retrieval.MultiRetrievalTests.post
    build = retrieval.MultiRetrievalTests.build

    def test_multi_route_uses_final_k_instead_of_hidden_metric_default(self):
        self.request['metrics'] = [{'id':'rules.recall_at_k', 'k':5}]
        run, get, _ = self.build()
        get.assert_not_called()
        metric = run['evidence']['metrics'][0]
        self.assertEqual((metric['k'], metric['k_source']), (2, 'fusion'))
        self.assertEqual(run['metric_results'][0]['score'], 1)


class RecallKUnitTests(unittest.TestCase):
    def test_legacy_fixed_cutoff_and_short_or_empty_retrieval(self):
        sample = {'annotations':{'relevant_chunk_ids':['sixth']}}
        observation = {'retrieved_chunk_ids':['a','b','c','d','e','sixth','g']}
        self.assertEqual(score_rule({'id':'rules.recall_at_k','k':5},sample,observation)[0], 0)
        self.assertEqual(score_rule({'id':'rules.recall_at_k','k':10},sample,observation)[0], 1)
        self.assertEqual(score_rule({'id':'rules.recall_at_k','k':10},sample,{'retrieved_chunk_ids':[]})[0], 0)

    def test_supported_dify_configuration_fields(self):
        for field in ('retrieval_model_dict','retrieval_model'):
            with patch('backend.evaluation_recall.requests.get', return_value=settings(10, field)):
                self.assertEqual(read_dify_top_k({'base_url':'https://dify.invalid/v1','dataset_id':'kb'}, 'key'),10)
