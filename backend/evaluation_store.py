"""Workspace-bound evaluation evidence and serialized worker transitions.

Workers must capture this concrete store before leaving the request context.
There is deliberately no execution scheduler in M0.
"""
import hashlib
import json
import math
import uuid

from fastapi import HTTPException

from backend.evaluation_contracts import CONTRACT_VERSION, dependency_check, generation_engine, safe_evidence, evaluation_type
from backend.store import utc_now
from backend.evaluation_jobs import EvaluationJobStore
from backend.dataset_annotations import current_annotation
from backend.evaluation_results import summarize_metrics

EVALUATION_TABLE_COLUMNS = {
    'dataset_versions': ('id', 'dataset_id', 'created_at', 'payload'),
    'evaluation_runs': ('id', 'created_at', 'updated_at', 'status', 'idempotency_key', 'request_hash', 'evidence', 'payload'),
    'evaluation_cases': ('id', 'run_id', 'payload'),
    'metric_results': ('id', 'run_id', 'case_id', 'payload'),
    'evaluation_cache': ('id', 'payload'),
    'dataset_annotations': ('id', 'dataset_id', 'payload'),
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def create_evaluation_tables(connection):
    for table, columns in EVALUATION_TABLE_COLUMNS.items():
        definitions = [f'{key} TEXT ' + ('PRIMARY KEY' if i == 0 else 'NOT NULL') for i, key in enumerate(columns)]
        connection.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(definitions)})")
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS evaluation_runs_idempotency ON evaluation_runs(idempotency_key)')
    connection.execute('CREATE INDEX IF NOT EXISTS evaluation_cases_run ON evaluation_cases(run_id)')
    connection.execute('CREATE INDEX IF NOT EXISTS metric_results_run ON metric_results(run_id,case_id)')
    connection.execute('CREATE INDEX IF NOT EXISTS dataset_versions_dataset ON dataset_versions(dataset_id,created_at)')
    # Immutable at the database boundary too; ordinary dataset deletion is safe.
    connection.execute("""CREATE OR REPLACE FUNCTION protect_evaluation_evidence() RETURNS trigger AS $$
        BEGIN
            IF TG_TABLE_NAME = 'dataset_versions' THEN
                RAISE EXCEPTION 'Dataset versions are immutable';
            END IF;
            IF NEW.evidence IS DISTINCT FROM OLD.evidence OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
                OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key THEN
                RAISE EXCEPTION 'Evaluation evidence is immutable';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql""")
    connection.execute('DROP TRIGGER IF EXISTS immutable_dataset_version ON dataset_versions')
    connection.execute('CREATE TRIGGER immutable_dataset_version BEFORE UPDATE OR DELETE ON dataset_versions FOR EACH ROW EXECUTE FUNCTION protect_evaluation_evidence()')
    connection.execute('DROP TRIGGER IF EXISTS immutable_run_evidence ON evaluation_runs')
    connection.execute('CREATE TRIGGER immutable_run_evidence BEFORE UPDATE ON evaluation_runs FOR EACH ROW EXECUTE FUNCTION protect_evaluation_evidence()')


class EvaluationStore(EvaluationJobStore):
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _row(connection, run_id, *, lock=False):
        row = connection.execute('SELECT * FROM evaluation_runs WHERE id=%s' + (' FOR UPDATE' if lock else ''), (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, '评测运行不存在')
        return row

    @staticmethod
    def _save_run(connection, run):
        run['updated_at'] = utc_now()
        connection.execute('UPDATE evaluation_runs SET updated_at=%s,status=%s,payload=%s WHERE id=%s',
                           (run['updated_at'], run['status'], encoded(run), run['id']))

    @staticmethod
    def _save_child(connection, table, value):
        assert table in {'evaluation_cases', 'metric_results'}
        connection.execute(f'UPDATE {table} SET payload=%s WHERE id=%s', (encoded(value), value['id']))

    @staticmethod
    def _children(connection, table, run_id):
        assert table in {'evaluation_cases', 'metric_results'}
        return [json.loads(row['payload']) for row in connection.execute(f'SELECT payload FROM {table} WHERE run_id=%s ORDER BY id', (run_id,)).fetchall()]

    def existing(self, key, request_hash):
        with self.store._session() as connection:
            row = connection.execute('SELECT * FROM evaluation_runs WHERE idempotency_key=%s FOR UPDATE', (key,)).fetchone()
            if row:
                if row['request_hash'] != request_hash:
                    raise HTTPException(409, '幂等键已用于不同的评测请求')
                return self._detail(connection, row)

    def prepare(self, request, request_hash, configuration):
        now, run_id, version_id = utc_now(), uuid.uuid4().hex, uuid.uuid4().hex
        with self.store._session() as connection:
            # DB-scoped transaction lock handles concurrent duplicate submissions,
            # even when different datasets were supplied for the same key.
            connection.execute('SELECT pg_advisory_xact_lock(hashtext(current_schema()), hashtext(%s))', (request.idempotency_key,))
            row = connection.execute('SELECT * FROM evaluation_runs WHERE idempotency_key=%s FOR UPDATE', (request.idempotency_key,)).fetchone()
            if row:
                if row['request_hash'] != request_hash:
                    raise HTTPException(409, '幂等键已用于不同的评测请求')
                return self._detail(connection, row)
            if request.dataset_version_id:
                row = connection.execute('SELECT payload FROM dataset_versions WHERE id=%s', (request.dataset_version_id,)).fetchone()
                if not row:
                    raise HTTPException(404, '数据集版本不存在')
                version = json.loads(row['payload'])
                if request.dataset_id and request.dataset_id != version['local_dataset_id']:
                    raise HTTPException(400, '版本与数据集不匹配')
                version_id = version['id']
            else:
                version = self._freeze(connection, request.dataset_id)
                version_id = version['id']
            if request.evaluation_type and version['dataset'].get('evaluation_purpose', 'both') not in {'both', request.evaluation_type}:
                raise HTTPException(400, '数据集用途与评测类型不匹配，请选择对应用途或两者通用的数据集')
            samples = list(version['samples'])
            samples = samples[:request.sample_limit] if request.sample_limit else samples
            if not samples:raise HTTPException(400, '所选场景和审核条件下没有可评测样本')
            evidence = {'contract_version': CONTRACT_VERSION, 'dataset_version_id': version_id,
                        'dataset_content_hash': version['content_hash'], **configuration}
            run = {'id': run_id, 'created_at': now, 'updated_at': now, 'status': 'prepared',
                   'attempt': 0, 'worker_token': None, 'error': None, 'case_count': len(samples),
                   'name': request.name, 'execution_available': True, 'queue_state': None, 'usage': {}}
            connection.execute('INSERT INTO evaluation_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               (run_id, now, now, run['status'], request.idempotency_key, request_hash, encoded(evidence), encoded(run)))
            for sample in samples:
                case = {'id': uuid.uuid4().hex, 'run_id': run_id, 'sample_id': sample['id'],
                        'status': 'pending', 'observation': None, 'error': None, 'history': []}
                connection.execute('INSERT INTO evaluation_cases VALUES (%s,%s,%s)', (case['id'], run_id, encoded(case)))
                for metric in evidence['metrics']:
                    check = dependency_check(metric, sample)
                    result = {'id': uuid.uuid4().hex, 'run_id': run_id, 'case_id': case['id'], 'metric_id': metric['id'],
                              'status': 'missing_data' if check['missing_fields'] else 'pending', 'dependency': check,
                              'score': None, 'passed': None, 'reason': None, 'error': None, 'usage': None, 'history': []}
                    connection.execute('INSERT INTO metric_results VALUES (%s,%s,%s,%s)', (result['id'], run_id, case['id'], encoded(result)))
            return self._detail(connection, self._row(connection, run_id))

    def _freeze(self, connection, dataset_id):
        row = connection.execute('SELECT payload FROM datasets WHERE id=%s FOR UPDATE', (dataset_id,)).fetchone()
        if not row:
            raise HTTPException(404, '数据集不存在')
        dataset = json.loads(row['payload'])
        if dataset['status'] == 'generating':
            raise HTTPException(409, '数据集正在构建，完成后可冻结版本')
        samples = [json.loads(row['payload']) for row in connection.execute(
            'SELECT payload FROM dataset_items WHERE dataset_id=%s ORDER BY created_at,id', (dataset_id,)).fetchall()]
        samples = [s for s in samples if s.get('status', 'active') == 'active']
        annotations = {r['id']: json.loads(r['payload']) for r in connection.execute(
            'SELECT id,payload FROM dataset_annotations WHERE dataset_id=%s', (dataset_id,)).fetchall()}
        source_row = connection.execute('SELECT payload FROM dataset_sources WHERE dataset_id=%s', (dataset_id,)).fetchone()
        source_snapshot = json.loads(source_row['payload']) if source_row else []
        # Backfill only exact context-to-source matches for older generated rows.
        # This is deterministic and does not mutate the live dataset or guess IDs.
        source_by_id = {str(c.get('id')): c for c in source_snapshot if c.get('id') and c.get('content')}
        for sample in samples:
            annotation = current_annotation(sample, annotations.get(sample['id'], {}))
            contexts = sample.get('context') or []
            if isinstance(contexts, str): contexts = [contexts]
            matched = [str((chunk.get('metadata') or {}).get('segment_id') or chunk_id) for chunk_id, chunk in source_by_id.items()
                       if any(str(context).strip() and (str(context).strip() == str(chunk.get('content')).strip()
                                                        or str(context).strip() in str(chunk.get('content')).strip())
                              for context in contexts)]
            if matched and not annotation.get('relevant_chunk_ids'):
                annotation['source_ids'] = list(dict.fromkeys(annotation.get('source_ids') or matched))
                annotation['relevant_chunk_ids'] = list(dict.fromkeys(matched))
                annotation['graded_relevance'] = annotation.get('graded_relevance') or {chunk_id: 1 for chunk_id in matched}
            sample['annotations'] = annotation
        samples = [safe_evidence(sample) for sample in samples]
        if not samples:
            raise HTTPException(400, '数据集没有启用的样本')
        version = {'id': uuid.uuid4().hex, 'local_dataset_id': dataset_id, 'created_at': utc_now(),
                   'contract_version': CONTRACT_VERSION, 'dataset': safe_evidence(dataset), 'samples': samples,
                   'sources': safe_evidence(source_snapshot) if source_snapshot else None,
                   'generation_engine': generation_engine(dataset.get('sdk'))}
        version['content_hash'] = fingerprint(version)
        connection.execute('INSERT INTO dataset_versions VALUES (%s,%s,%s,%s)',
                           (version['id'], dataset_id, version['created_at'], encoded(version)))
        return version

    def freeze(self, dataset_id):
        with self.store._session() as connection:
            return self._freeze(connection, dataset_id)

    def _detail(self, connection, row):
        return {**json.loads(row['payload']), 'evidence': json.loads(row['evidence']),
                'cases': self._children(connection, 'evaluation_cases', row['id']),
                'metric_results': self._children(connection, 'metric_results', row['id'])}

    def get(self, run_id):
        with self.store._session() as connection:
            # Parent lock also prevents reading mismatched case/metric transitions.
            return self._detail(connection, self._row(connection, run_id, lock=True))

    def list(self, limit=50, dataset_id=None):
        # Resolve ownership from immutable versions, including records created before
        # datasets became the evaluation entry point. Filter before applying the limit.
        query = """SELECT r.payload,r.evidence,v.dataset_id,v.id AS dataset_version_id,
                          v.payload::jsonb->'dataset'->>'name' AS dataset_name
                   FROM evaluation_runs r LEFT JOIN dataset_versions v
                   ON v.id=r.evidence::jsonb->>'dataset_version_id'"""
        params = []
        if dataset_id is not None:
            query += ' WHERE v.dataset_id=%s'
            params.append(dataset_id)
        query += ' ORDER BY r.created_at DESC,r.id DESC LIMIT %s'
        params.append(limit)
        with self.store._session() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
            if not rows:
                return []
            runs = [json.loads(row['payload']) for row in rows]
            run_ids = [run['id'] for run in runs]
            metric_results = {run_id: [] for run_id in run_ids}
            for metric_row in connection.execute(
                    'SELECT run_id,payload FROM metric_results WHERE run_id=ANY(%s)', (run_ids,)).fetchall():
                metric_results[metric_row['run_id']].append(json.loads(metric_row['payload']))
            summaries = {}
            for run, row in zip(runs, rows):
                evidence = json.loads(row['evidence'])
                summaries[run['id']] = {'metrics': summarize_metrics(evidence.get('metrics', []), metric_results[run['id']])}
            return [{**run, 'evaluation_type': evaluation_type(json.loads(row['evidence'])),
                     'dataset_id': row['dataset_id'], 'dataset_version_id': row['dataset_version_id'],
                     'dataset_name': row['dataset_name'], 'summary': summaries[run['id']]}
                    for run, row in zip(runs, rows)]

    def version(self, version_id):
        with self.store._session() as connection:
            row = connection.execute('SELECT payload FROM dataset_versions WHERE id=%s', (version_id,)).fetchone()
            if not row:
                raise HTTPException(404, '数据集版本不存在')
            return json.loads(row['payload'])

    def rerun(self, run_id, *, recall_cutoff=None):
        """Reuse frozen samples and config, refreshing Dify's Recall@K cutoff when supplied."""
        with self.store._session() as connection:
            original_row = self._row(connection, run_id, lock=True)
            original = json.loads(original_row['payload'])
            evidence = json.loads(original_row['evidence'])
            if recall_cutoff:
                for metric in evidence['metrics']:
                    if metric['id'] == 'rules.recall_at_k':
                        metric.update(recall_cutoff)
            if original.get('execution_available') is not True:
                raise HTTPException(409, '旧版评测记录不能重新评测，请新建评测')
            version_row = connection.execute(
                'SELECT payload FROM dataset_versions WHERE id=%s', (evidence['dataset_version_id'],)
            ).fetchone()
            if not version_row:
                raise HTTPException(404, '评测数据集版本不存在')
            version = json.loads(version_row['payload'])
            samples = {sample['id']: sample for sample in version['samples']}
            original_cases = self._children(connection, 'evaluation_cases', run_id)
            sample_ids = [case['sample_id'] for case in original_cases if case['sample_id'] in samples]
            if not sample_ids:
                raise HTTPException(400, '原评测没有可重新评测的样本')

            now, new_id = utc_now(), uuid.uuid4().hex
            name = f"{original.get('name') or 'Quality evaluation'} · 重新评测"
            request_key = f"rerun:{run_id}:{new_id}"
            request_hash = fingerprint({'rerun_of': run_id, 'evidence': evidence, 'sample_ids': sample_ids})
            run = {'id': new_id, 'created_at': now, 'updated_at': now, 'status': 'prepared',
                   'attempt': 0, 'worker_token': None, 'error': None, 'case_count': len(sample_ids),
                   'name': name, 'execution_available': True, 'queue_state': None, 'usage': {},
                   'rerun_of': run_id}
            connection.execute('INSERT INTO evaluation_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               (new_id, now, now, run['status'], request_key, request_hash,
                                encoded(evidence), encoded(run)))
            for sample_id in sample_ids:
                case = {'id': uuid.uuid4().hex, 'run_id': new_id, 'sample_id': sample_id,
                        'status': 'pending', 'observation': None, 'error': None, 'history': []}
                connection.execute('INSERT INTO evaluation_cases VALUES (%s,%s,%s)',
                                   (case['id'], new_id, encoded(case)))
                sample = samples[sample_id]
                for metric in evidence['metrics']:
                    check = dependency_check(metric, sample)
                    result = {'id': uuid.uuid4().hex, 'run_id': new_id, 'case_id': case['id'],
                              'metric_id': metric['id'],
                              'status': 'missing_data' if check['missing_fields'] else 'pending',
                              'dependency': check, 'score': None, 'passed': None, 'reason': None,
                              'error': None, 'usage': None, 'history': []}
                    connection.execute('INSERT INTO metric_results VALUES (%s,%s,%s,%s)',
                                       (result['id'], new_id, case['id'], encoded(result)))
            return self._detail(connection, self._row(connection, new_id))

    def latest_summaries(self, dataset_ids):
        if not dataset_ids:
            return {}
        # Select per dataset before any limit. Ownership comes from frozen versions,
        # so legacy runs and runs reusing an existing version are included too.
        with self.store._session() as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            rows = connection.execute("""
                SELECT DISTINCT ON (v.dataset_id) v.dataset_id, r.id, r.payload, r.evidence
                FROM evaluation_runs r JOIN dataset_versions v
                  ON v.id=r.evidence::jsonb->>'dataset_version_id'
                WHERE v.dataset_id=ANY(%s)
                ORDER BY v.dataset_id, r.created_at DESC, r.id DESC
                """, (dataset_ids,)).fetchall()
            results = {row['id']: [] for row in rows}
            if not rows:
                return {}
            for row in connection.execute(
                    """SELECT run_id, payload::jsonb->>'metric_id' AS metric_id,
                              payload::jsonb->>'status' AS status, payload::jsonb->'score' AS score
                       FROM metric_results WHERE run_id=ANY(%s)""", (list(results),)).fetchall():
                results[row['run_id']].append(row)
            summaries = {}
            for row in rows:
                run, evidence = json.loads(row['payload']), json.loads(row['evidence'])
                summaries[row['dataset_id']] = {
                    key: run[key] for key in ('id', 'name', 'status', 'created_at', 'case_count')}
                summaries[row['dataset_id']]['metrics'] = summarize_metrics(evidence['metrics'], results[row['id']])
            return summaries

    def delete(self, run_id):
        # Serialize with enqueue, claim and worker writes using the same parent lock.
        with self.store._session() as connection:
            run = json.loads(self._row(connection, run_id, lock=True)['payload'])
            if run['status'] == 'running' or (run['status'] == 'prepared' and run.get('queue_state')):
                raise HTTPException(409, '请先取消排队中或执行中的评测，再删除记录')
            for table in ('metric_results', 'evaluation_cases'):
                connection.execute(f'DELETE FROM {table} WHERE run_id=%s', (run_id,))
            connection.execute('DELETE FROM evaluation_runs WHERE id=%s', (run_id,))
        return {'deleted': True}

    def cancel(self, run_id):
        with self.store._session() as connection:
            run = json.loads(self._row(connection, run_id, lock=True)['payload'])
            if run['status'] in {'prepared', 'running'}:
                run.update(status='cancelled', worker_token=None)
                for table in ('evaluation_cases', 'metric_results'):
                    for item in self._children(connection, table, run_id):
                        if item['status'] in {'pending', 'running'}:
                            item['status'] = 'cancelled'
                            self._save_child(connection, table, item)
                self._save_run(connection, run)
            return self._detail(connection, self._row(connection, run_id))

    def start(self, run_id):
        """Internal worker contract, intentionally not an HTTP execution endpoint."""
        with self.store._session() as connection:
            run = json.loads(self._row(connection, run_id, lock=True)['payload'])
            if run['status'] != 'prepared':
                raise HTTPException(409, '只有已准备的评测可以启动')
            run.update(status='running', attempt=run['attempt'] + 1, worker_token=uuid.uuid4().hex)
            self._save_run(connection, run)
            return run['worker_token']

    def _worker(self, connection, run_id, token):
        row = self._row(connection, run_id, lock=True)
        run = json.loads(row['payload'])
        if run['status'] != 'running' or not token or token != run['worker_token']:
            raise HTTPException(409, '评测已停止或工作令牌已失效')
        return run, json.loads(row['evidence'])

    def record_target(self, run_id, case_id, token, *, observation=None, error=False):
        with self.store._session() as connection:
            run, evidence = self._worker(connection, run_id, token)
            cases = self._children(connection, 'evaluation_cases', run_id)
            case = next((c for c in cases if c['id'] == case_id), None)
            if not case:
                raise HTTPException(404, '评测样本不存在')
            if case['status'] != 'pending':
                raise HTTPException(409, '目标结果已提交')
            # Only normalized target evidence enters storage; transport errors must
            # never persist exception text containing headers, URLs or credentials.
            observation = observation or {}
            output, contexts = observation.get('actual_output'), observation.get('retrieval_context')
            if output is not None and (not isinstance(output, str) or len(output) > 200000):
                raise ValueError('Invalid actual output')
            if contexts is not None and (not isinstance(contexts, list) or any(not isinstance(c, str) for c in contexts) or sum(map(len, contexts)) > 1000000):
                raise ValueError('Invalid retrieval context')
            case.update(status='target_error' if error else 'completed', error='target_call_failed' if error else None,
                        observation=None if error else {'actual_output': output, 'retrieval_context': contexts,
                                                       'metadata': safe_evidence(observation.get('metadata', {})),
                                                       'duration_ms': observation.get('duration_ms'), 'usage': observation.get('usage'),
                                                       'retrieved_chunk_ids': observation.get('retrieved_chunk_ids'),
                                                       'turns': safe_evidence(observation.get('turns')), 'tools_called': safe_evidence(observation.get('tools_called'))})
            self._save_child(connection, 'evaluation_cases', case)
            version = json.loads(connection.execute('SELECT payload FROM dataset_versions WHERE id=%s', (evidence['dataset_version_id'],)).fetchone()['payload'])
            sample = next(s for s in version['samples'] if s['id'] == case['sample_id'])
            for result in self._children(connection, 'metric_results', run_id):
                if result['case_id'] != case_id:
                    continue
                if error:
                    result.update(status='missing_data', error='target_call_failed')
                else:
                    metric = next(m for m in evidence['metrics'] if m['id'] == result['metric_id'])
                    check = dependency_check(metric, sample, case['observation'])
                    result.update(dependency=check, status='missing_data' if check['missing_fields'] else 'pending')
                self._save_child(connection, 'metric_results', result)
            self._settle(connection, run)

    def record_metric(self, run_id, result_id, token, *, score=None, reason=None, error=False, usage=None):
        with self.store._session() as connection:
            run, evidence = self._worker(connection, run_id, token)
            result = next((r for r in self._children(connection, 'metric_results', run_id) if r['id'] == result_id), None)
            if not result:
                raise HTTPException(404, '指标结果不存在')
            case = next(c for c in self._children(connection, 'evaluation_cases', run_id) if c['id'] == result['case_id'])
            if result['status'] != 'pending' or case['status'] != 'completed':
                raise HTTPException(409, '指标不可提交或已提交')
            metric = next(m for m in evidence['metrics'] if m['id'] == result['metric_id'])
            if not error and (type(score) not in {int, float} or not math.isfinite(score) or not metric['score_range'][0] <= score <= metric['score_range'][1]):
                raise ValueError('Metric score outside declared range')
            result.update(status='judge_error' if error else 'completed', score=None if error else score,
                          passed=None if error else score >= metric['threshold']['value'],
                          reason=None if error else reason, error='judge_call_failed' if error else None,
                          usage=safe_evidence(usage) if usage else None)
            self._save_child(connection, 'metric_results', result)
            self._settle(connection, run)

    def _settle(self, connection, run):
        cases = self._children(connection, 'evaluation_cases', run['id'])
        metrics = self._children(connection, 'metric_results', run['id'])
        if any(c['status'] == 'pending' for c in cases) or any(m['status'] == 'pending' for m in metrics):
            self._save_run(connection, run)
            return
        completed = sum(m['status'] == 'completed' for m in metrics)
        all_targets = all(c['status'] == 'completed' for c in cases)
        status = 'completed' if completed == len(metrics) and all_targets else 'partial_failed' if completed or not metrics and any(c['status']=='completed' for c in cases) else 'failed'
        run.update(status=status, worker_token=None, queue_state='finished', lease_until=None)
        self._save_run(connection, run)

    def retry_failed(self, run_id):
        with self.store._session() as connection:
            run = json.loads(self._row(connection, run_id, lock=True)['payload'])
            if run['status'] not in {'failed', 'partial_failed'}:
                raise HTTPException(409, '只能重试失败项')
            cases = self._children(connection, 'evaluation_cases', run_id)
            failed_cases = {c['id'] for c in cases if c['status'] == 'target_error'}
            results = self._children(connection, 'metric_results', run_id)
            failed_metrics = [m for m in results if m['case_id'] in failed_cases or m['status'] == 'judge_error']
            if not failed_cases and not failed_metrics:
                raise HTTPException(409, '缺少不可变参考数据，无法通过重试修复')
            for table, items in (('evaluation_cases', [c for c in cases if c['id'] in failed_cases]), ('metric_results', failed_metrics)):
                for item in items:
                    history = item['history']
                    history.append({'attempt': run['attempt'], **{k: v for k, v in item.items() if k != 'history'}})
                    item.update(status='pending', error=None)
                    if table == 'metric_results':
                        item.update(score=None, passed=None, reason=None, usage=None)
                    self._save_child(connection, table, item)
            run.update(status='prepared', error=None, worker_token=None)
            self._save_run(connection, run)
            return self._detail(connection, self._row(connection, run_id))

    def recover_interrupted(self):
        with self.store._session() as connection:
            rows = connection.execute("SELECT * FROM evaluation_runs WHERE status='running' AND COALESCE(payload::jsonb->>'queue_state','')<>'leased' FOR UPDATE").fetchall()
            for row in rows:
                run = json.loads(row['payload'])
                for table in ('evaluation_cases', 'metric_results'):
                    for item in self._children(connection, table, run['id']):
                        if item['status'] in {'pending', 'running'}:
                            item.update(status='target_error' if table == 'evaluation_cases' else 'judge_error', error='server_restarted')
                            self._save_child(connection, table, item)
                run.update(status='failed', worker_token=None, error='server_restarted')
                self._save_run(connection, run)
