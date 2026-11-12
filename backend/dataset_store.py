"""Dataset persistence. All writes lock the parent to serialize edits and generation."""
import json
import uuid

from fastapi import HTTPException

from backend.store import utc_now
from backend.dataset_golden import canonical_item
from shared.request_control import GenerationStopped


class DatasetStore:
    def __init__(self, store):
        self.store = store

    def create(self, values):
        now = utc_now()
        dataset = {"id": uuid.uuid4().hex[:12], "created_at": now, "updated_at": now,
                   "evaluation_purpose": "both", "status": "ready", "item_count": 0, "error": None, **values}
        with self.store._session() as connection:
            connection.execute("INSERT INTO datasets VALUES (%s,%s,%s,%s)",
                               (dataset['id'], now, now, json.dumps(dataset, ensure_ascii=False)))
        return dataset

    def get(self, identifier):
        with self.store._session() as connection:
            row = connection.execute("SELECT payload FROM datasets WHERE id=%s", (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, "数据集不存在")
        return {'evaluation_purpose': 'both', **json.loads(row['payload'])}

    def page(self, page, size, q, purpose=None):
        result = self.store._page("datasets", "updated_at", page, size, q=q, evaluation_purpose=purpose)
        result["items"] = [{"evaluation_purpose": "both", **item} for item in result["items"]]
        return result

    def source_summaries(self, datasets):
        from backend.dataset_sources import summarize_sources
        if not datasets:
            return {}
        with self.store._session() as connection:
            rows = connection.execute("""SELECT dataset_id,
                jsonb_agg(jsonb_build_object('metadata', chunk->'metadata', 'content', '')) AS chunks
                FROM dataset_sources CROSS JOIN LATERAL jsonb_array_elements(payload::jsonb) AS chunk
                WHERE dataset_id=ANY(%s) AND chunk ? 'content' GROUP BY dataset_id""",
                ([item['id'] for item in datasets],)).fetchall()
        chunks = {row['dataset_id']: row['chunks'] for row in rows}
        configs = {item['id']: item for item in self.store.list_service_configs() if item.get('provider') == 'dify'}
        return {item['id']: summarize_sources(chunks.get(item['id'], []), item, configs) for item in datasets}

    def items(self, identifier, page=1, size=20, q=""):
        self.get(identifier)
        with self.store._session() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            args = (identifier, q)
            where = "dataset_id=%s AND strpos(lower(payload), lower(%s))>0"
            total = connection.execute("SELECT count(*) AS n FROM dataset_items WHERE " + where, args).fetchone()['n']
            page = min(page, max(1, (total + size - 1) // size))
            rows = connection.execute("SELECT payload FROM dataset_items WHERE " + where + " ORDER BY created_at,id LIMIT %s OFFSET %s", (*args, size, (page - 1) * size)).fetchall()
        return {"items": [canonical_item(json.loads(r['payload'])) for r in rows], "total": total, "page": page, "page_size": size}

    def mutate(self, identifier, action, *, generating=False, build_token=None):
        with self.store._session() as connection:
            row = connection.execute("SELECT payload FROM datasets WHERE id=%s FOR UPDATE", (identifier,)).fetchone()
            if not row:
                raise HTTPException(404, "数据集不存在")
            dataset = json.loads(row['payload'])
            if build_token is not None and (dataset.get('build_token') != build_token or dataset['status'] == 'cancelled'):
                raise GenerationStopped()
            if dataset['status'] == 'generating' and not generating:
                raise HTTPException(409, "数据集正在构建，请等待完成后再修改")
            action(connection, dataset)
            dataset['updated_at'] = utc_now()
            connection.execute("UPDATE datasets SET updated_at=%s,payload=%s WHERE id=%s",
                               (dataset['updated_at'], json.dumps(dataset, ensure_ascii=False), identifier))
        return dataset

    @staticmethod
    def is_generated(item, dataset):
        # SDK metadata can survive exports, imports and manual edits. Only the
        # application-owned origin proves that this record is safe to replace.
        return item.get('origin') == 'generated'

    @staticmethod
    def insert_items(connection, dataset, items, origin):
        if dataset['item_count'] + len(items) > 20000:
            raise HTTPException(400, "每个数据集最多支持 20000 条数据")
        now = utc_now()
        for item in items:
            item = {**canonical_item(item), 'origin': origin, 'id': uuid.uuid4().hex,
                    'dataset_id': dataset['id'], 'created_at': now, 'updated_at': now}
            connection.execute("INSERT INTO dataset_items VALUES (%s,%s,%s,%s,%s)",
                               (item['id'], dataset['id'], now, now, json.dumps(item, ensure_ascii=False)))
        dataset['item_count'] += len(items)

    def add(self, identifier, items, *, generating=False, origin='manual', build_token=None):
        def action(connection, dataset):
            self.insert_items(connection, dataset, items, 'generated' if generating else origin)
        return self.mutate(identifier, action, generating=generating, build_token=build_token)

    def replace_generated(self, identifier, items, *, build_token=None):
        def action(connection, dataset):
            rows = connection.execute('SELECT id,payload FROM dataset_items WHERE dataset_id=%s', (identifier,)).fetchall()
            ids = [row['id'] for row in rows if self.is_generated(json.loads(row['payload']), dataset)]
            connection.execute('DELETE FROM dataset_items WHERE dataset_id=%s AND id=ANY(%s)', (identifier, ids))
            dataset['item_count'] -= len(ids)
            self.insert_items(connection, dataset, items, 'generated')
            dataset.update(status='ready', error=None, build_progress=len(items))
        return self.mutate(identifier, action, generating=True, build_token=build_token)

    def update_item(self, identifier, item_id, values=None):
        def action(connection, dataset):
            row = connection.execute("SELECT payload FROM dataset_items WHERE dataset_id=%s AND id=%s", (identifier, item_id)).fetchone()
            if not row:
                raise HTTPException(404, "数据项不存在")
            if values is None:
                connection.execute("DELETE FROM dataset_items WHERE dataset_id=%s AND id=%s", (identifier, item_id))
                dataset['item_count'] -= 1
            else:
                # PUT replaces Golden content so cleared optional fields do not survive.
                old = json.loads(row['payload'])
                item = {**canonical_item(values), **{k: old[k] for k in ('id', 'dataset_id', 'created_at')},
                        'origin': 'manual', 'updated_at': utc_now(),
                        **{k: old[k] for k in ('generation_trace_id', 'generation_record_id') if k in old}}
                connection.execute("UPDATE dataset_items SET updated_at=%s,payload=%s WHERE dataset_id=%s AND id=%s",
                                   (item['updated_at'], json.dumps(item, ensure_ascii=False), identifier, item_id))
        return self.mutate(identifier, action)

    def selection(self, identifier, q=""):
        self.get(identifier)
        with self.store._session() as connection:
            rows = connection.execute("SELECT id FROM dataset_items WHERE dataset_id=%s AND strpos(lower(payload), lower(%s))>0 ORDER BY id", (identifier, q)).fetchall()
        return {"ids": [row['id'] for row in rows], "total": len(rows)}

    def bulk_items(self, identifier, ids, operation):
        result = {'succeeded': [], 'skipped': [], 'failed': []}
        def action(connection, dataset):
            rows = connection.execute("SELECT id,payload FROM dataset_items WHERE dataset_id=%s AND id=ANY(%s)", (identifier, ids)).fetchall()
            if len(rows) != len(ids):
                raise HTTPException(404, "部分数据项已不存在，请刷新列表后重试")
            now = utc_now()
            if operation == 'delete':
                connection.execute("DELETE FROM dataset_items WHERE dataset_id=%s AND id=ANY(%s)", (identifier, ids))
                dataset['item_count'] -= len(rows)
                result['succeeded'] = ids
                return
            status = 'active' if operation == 'enable' else 'archived'
            for row in rows:
                item = json.loads(row['payload'])
                if item.get('status', 'active') == status:
                    result['skipped'].append(row['id'])
                    continue
                item.update(status=status, origin='manual', updated_at=now)
                connection.execute("UPDATE dataset_items SET updated_at=%s,payload=%s WHERE dataset_id=%s AND id=%s", (now, json.dumps(item, ensure_ascii=False), identifier, row['id']))
                result['succeeded'].append(row['id'])
        self.mutate(identifier, action)
        return result

    def selected_items(self, identifier, ids):
        self.get(identifier)
        with self.store._session() as connection:
            rows = connection.execute("SELECT payload FROM dataset_items WHERE dataset_id=%s AND id=ANY(%s) ORDER BY created_at,id", (identifier, ids)).fetchall()
        if len(rows) != len(ids):
            raise HTTPException(404, "部分数据项已不存在，请刷新列表后重试")
        return [canonical_item(json.loads(row['payload'])) for row in rows]

    def delete(self, identifier):
        def action(connection, dataset):
            connection.execute("DELETE FROM dataset_items WHERE dataset_id=%s", (identifier,))
            connection.execute("DELETE FROM dataset_sources WHERE dataset_id=%s", (identifier,))
            connection.execute("DELETE FROM dataset_generation_records WHERE dataset_id=%s", (identifier,))
            connection.execute("DELETE FROM dataset_generation_traces WHERE dataset_id=%s", (identifier,))
            connection.execute("DELETE FROM datasets WHERE id=%s", (identifier,))
        self.mutate(identifier, action)

    def sources(self, identifier):
        with self.store._session() as connection:
            row = connection.execute("SELECT payload FROM dataset_sources WHERE dataset_id=%s", (identifier,)).fetchone()
        return json.loads(row['payload']) if row else []

    def save_sources(self, identifier, chunks, *, build_token=None):
        def action(connection, dataset):
            connection.execute("INSERT INTO dataset_sources VALUES (%s,%s) ON CONFLICT (dataset_id) DO UPDATE SET payload=EXCLUDED.payload", (identifier, json.dumps(chunks, ensure_ascii=False)))

        return self.mutate(identifier, action, generating=True, build_token=build_token)
