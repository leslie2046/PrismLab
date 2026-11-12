"""PostgreSQL leases and budgets; all methods use the captured workspace store."""
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from backend.store import utc_now


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class EvaluationJobStore:
    def enqueue(self, run_id):
        with self.store._session() as c:
            row = self._row(c, run_id, lock=True)
            run, evidence = json.loads(row['payload']), json.loads(row['evidence'])
            if evidence.get('contract_version') != '2.0' or not run.get('execution_available'):
                raise HTTPException(409, '旧契约不能执行，请按当前指标实现重新创建评测')
            if run['status'] == 'prepared':
                run.update(queue_state='queued', error=None)
                self._save_run(c, run)
            elif run['status'] != 'running':
                raise HTTPException(409, '只有已准备的评测可以排队')
        return self.get(run_id)

    def claim(self, owner):
        with self.store._session() as c:
            row = c.execute("""SELECT * FROM evaluation_runs WHERE
                (status='prepared' AND payload::jsonb->>'queue_state'='queued') OR
                (status='running' AND payload::jsonb->>'queue_state'='leased' AND
                 (payload::jsonb->>'lease_until')::timestamptz < now())
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if not row:
                return None
            run = json.loads(row['payload'])
            run.update(status='running', queue_state='leased', lease_owner=owner,
                       lease_until=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),
                       worker_token=uuid.uuid4().hex, attempt=run['attempt']+1)
            self._save_run(c, run)
            return {'id': run['id'], 'worker_token': run['worker_token']}

    def heartbeat(self, run_id, token):
        with self.store._session() as c:
            run, _ = self._worker(c, run_id, token)
            run['lease_until'] = (datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()
            self._save_run(c, run)

    def reserve_request(self, run_id, token, stage, max_output):
        with self.store._session() as c:
            run, evidence = self._worker(c, run_id, token)
            budget = evidence['budget']
            usage = run.setdefault('usage', {})
            total_requests = sum(v.get('requests', 0) for v in usage.values())
            reserved = sum(v.get('reserved_output_tokens', 0) for v in usage.values())
            if total_requests >= budget['max_requests'] or reserved + max_output > budget['max_output_tokens']:
                raise HTTPException(402, 'evaluation_budget_exhausted')
            current = usage.setdefault(stage, {'requests': 0, 'input_tokens': None, 'output_tokens': None,
                                              'reserved_output_tokens': 0, 'cost': None})
            current['requests'] += 1
            current['reserved_output_tokens'] += max_output
            self._save_run(c, run)

    def add_usage(self, run_id, token, stage, usage):
        with self.store._session() as c:
            run, _ = self._worker(c, run_id, token)
            current = run.setdefault('usage', {}).setdefault(stage, {})
            for key in ('input_tokens', 'output_tokens'):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    current[key] = (current.get(key) or 0) + value
            self._save_run(c, run)

    def abort_pending(self, run_id, token, code):
        """Safe codes only; never persist transport exception strings."""
        allowed = {'budget_exhausted', 'authentication_failed', 'quota_exhausted', 'invalid_request',
                   'reranker_input_too_long', 'reranker_request_rejected',
                   'dify_retrieval_configuration_missing', 'dify_retrieval_rejected',
                   'service_unavailable', 'configuration_changed', 'worker_failed','unsupported_sample_kind','invalid_target_response','missing_conversation_id','unsupported_target','response_too_large','unexpected_redirect','empty_normalized_transcript','transcript_too_long'}
        code = code if code in allowed else 'worker_failed'
        with self.store._session() as c:
            run, _ = self._worker(c, run_id, token)
            for table in ('evaluation_cases', 'metric_results'):
                for item in self._children(c, table, run_id):
                    if item['status'] == 'pending':
                        item.update(status='target_error' if table == 'evaluation_cases' else 'judge_error', error=code)
                        self._save_child(c, table, item)
            run.update(error=code, queue_state='finished')
            self._settle(c, run)

    def cache_get(self, key):
        with self.store._session() as c:
            row = c.execute('SELECT payload FROM evaluation_cache WHERE id=%s', (key,)).fetchone()
        return json.loads(row['payload']) if row else None

    def cache_put(self, key, value):
        with self.store._session() as c:
            c.execute('INSERT INTO evaluation_cache VALUES (%s,%s) ON CONFLICT(id) DO NOTHING', (key, dumps(value)))
