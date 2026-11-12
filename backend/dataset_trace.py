"""Workspace-local, immutable generation inputs and per-sample snapshots."""
import json
import threading
import uuid

from fastapi import HTTPException

from backend.store import utc_now
from backend.dataset_golden import canonical_item


class GenerationTrace:
    def __init__(self, repo, identifier, snapshot):
        self.repo, self.identifier = repo, identifier
        self.id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.sequence = 0
        value = {'id': self.id, 'created_at': utc_now(), **snapshot}
        self.repo.mutate(identifier, lambda c, d: c.execute(
            'INSERT INTO dataset_generation_traces VALUES (%s,%s,%s,%s)',
            (self.id, identifier, value['created_at'], json.dumps(value, ensure_ascii=False))), generating=True)

    def record(self, kind, value):
        identifier, now = uuid.uuid4().hex, utc_now()
        payload = {'id': identifier, 'created_at': now, **value}
        self.repo.mutate(self.identifier, lambda c, d: c.execute(
            'INSERT INTO dataset_generation_records VALUES (%s,%s,%s,%s,%s,%s)',
            (identifier, self.identifier, self.id, kind, now, json.dumps(payload, ensure_ascii=False))), generating=True)
        return identifier

    def request(self, payload, role):
        # The payload contains only messages, model and inference parameters.
        # Never capture authorization headers, credentials or provider URLs.
        with self.lock:
            self.sequence += 1
            return self.record('request', {'sequence': self.sequence, 'role': role, 'request': payload})

    def attach(self, items):
        for item in items:
            snapshot = {key: item.get(key) for key in ('input', 'expected_output', 'context')}
            record_id = self.record('sample', snapshot)
            item.update(generation_trace_id=self.id, generation_record_id=record_id)
        return items


class DatasetTraceStore:
    def __init__(self, repo):
        self.repo = repo

    def _item(self, dataset_id, item_id):
        self.repo.get(dataset_id)
        with self.repo.store._session() as connection:
            row = connection.execute('SELECT payload FROM dataset_items WHERE dataset_id=%s AND id=%s', (dataset_id, item_id)).fetchone()
        if not row:
            raise HTTPException(404, '数据项不存在')
        return canonical_item(json.loads(row['payload']))

    def _trace_id(self, dataset_id, item_id):
        item = self._item(dataset_id, item_id)
        trace_id, record_id = item.get('generation_trace_id'), item.get('generation_record_id')
        if not trace_id or not record_id:
            return None
        with self.repo.store._session() as connection:
            row = connection.execute("SELECT id FROM dataset_generation_records WHERE dataset_id=%s AND trace_id=%s AND id=%s AND kind='sample'",
                                     (dataset_id, trace_id, record_id)).fetchone()
        return trace_id if row else None

    def get(self, dataset_id, item_id):
        item = self._item(dataset_id, item_id)
        result = {'availability': 'unavailable', 'sample': {k: item.get(k) for k in ('input', 'expected_output', 'context')},
                  'edited': False, 'trace': None}
        trace_id, record_id = item.get('generation_trace_id'), item.get('generation_record_id')
        if not trace_id or not record_id:
            return result
        with self.repo.store._session() as connection:
            trace = connection.execute('SELECT payload FROM dataset_generation_traces WHERE dataset_id=%s AND id=%s', (dataset_id, trace_id)).fetchone()
            sample = connection.execute("SELECT payload FROM dataset_generation_records WHERE dataset_id=%s AND trace_id=%s AND id=%s AND kind='sample'", (dataset_id, trace_id, record_id)).fetchone()
        if not trace or not sample:
            return result
        original = json.loads(sample['payload'])
        return {'availability': 'recorded', 'sample': original,
                'edited': any(item.get(key) != original.get(key) for key in ('input', 'expected_output', 'context')),
                'trace': json.loads(trace['payload'])}

    def requests(self, dataset_id, item_id, page, size):
        trace_id = self._trace_id(dataset_id, item_id)
        if not trace_id:
            return {'items': [], 'total': 0, 'page': 1, 'page_size': size}
        with self.repo.store._session() as connection:
            args = (dataset_id, trace_id)
            total = connection.execute("SELECT count(*) AS n FROM dataset_generation_records WHERE dataset_id=%s AND trace_id=%s AND kind='request'", args).fetchone()['n']
            page = min(page, max(1, (total + size - 1) // size))
            rows = connection.execute("""SELECT id,created_at,payload::jsonb->>'sequence' AS sequence,
                payload::jsonb->>'role' AS role,payload::jsonb->'request'->>'model' AS model
                FROM dataset_generation_records WHERE dataset_id=%s AND trace_id=%s AND kind='request'
                ORDER BY (payload::jsonb->>'sequence')::int,id LIMIT %s OFFSET %s""", (*args, size, (page-1)*size)).fetchall()
        return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': size}

    def request(self, dataset_id, item_id, request_id):
        trace_id = self._trace_id(dataset_id, item_id)
        if not trace_id:
            raise HTTPException(404, '生成请求记录不存在')
        with self.repo.store._session() as connection:
            row = connection.execute("SELECT payload FROM dataset_generation_records WHERE dataset_id=%s AND trace_id=%s AND id=%s AND kind='request'", (dataset_id, trace_id, request_id)).fetchone()
        if not row:
            raise HTTPException(404, '生成请求记录不存在')
        return json.loads(row['payload'])
