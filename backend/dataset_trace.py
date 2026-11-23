"""Workspace-local, immutable generation inputs and per-sample snapshots."""
import json
import re
import threading
import uuid

from fastapi import HTTPException

from backend.store import utc_now
from backend.dataset_golden import canonical_item


def _compact_text(value):
    """Normalize model prompts enough to compare copied source chunks safely."""
    # Prompt templates often add a colon, brackets or terminal punctuation
    # around a copied chunk. Ignore those separators while retaining Chinese,
    # Latin and numeric content so a source chunk still has to match its text.
    return re.sub(r'[^\w\u4e00-\u9fff]+', '', str(value or '')).casefold()


def _request_messages(payload):
    request = payload.get('request') if isinstance(payload, dict) else None
    request = request if isinstance(request, dict) else {}
    messages = request.get('messages') or []
    if not isinstance(messages, list):
        messages = []
    return request, [message for message in messages
                     if isinstance(message, dict) and isinstance(message.get('content'), str)]


def _match_request(payload, sample):
    """Score a recorded request against the sample it is being displayed for.

    A generation trace is usually a batch: one request can contain the source
    material used for several generated samples.  We therefore use the match
    only to select the most useful request snapshot and expose the scope to the
    UI instead of pretending that a batch request belongs to one sample.
    """
    request, messages = _request_messages(payload)
    user_text = '\n'.join(message['content'] for message in messages if message.get('role') == 'user')
    searchable = _compact_text(user_text or '\n'.join(message['content'] for message in messages))
    contexts = sample.get('context') or []
    if isinstance(contexts, str):
        contexts = [contexts]
    context_hits = [context for context in contexts
                    if _compact_text(context) and _compact_text(context) in searchable]
    sample_input = _compact_text(sample.get('input'))
    input_match = bool(sample_input and sample_input in searchable)
    role = payload.get('role') or 'generation'
    role_priority = 1 if role in {'generation', 'answer_generation'} else 0
    # A question match is stronger than a context match. Contexts are commonly
    # shared by every request in a batch, while the generated question is not.
    score = (1000 if input_match else 0) + len(context_hits) * 10 + role_priority
    return {
        'payload': payload, 'request': request, 'messages': messages,
        'score': score, 'input_match': input_match,
        'context_matches': len(context_hits), 'context_total': len(contexts),
        'role': role, 'sequence': payload.get('sequence'),
    }


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
            request_rows = connection.execute("""SELECT payload
                FROM dataset_generation_records
                WHERE dataset_id=%s AND trace_id=%s AND kind='request'
                ORDER BY (payload::jsonb->>'sequence')::int, id""", (dataset_id, trace_id)).fetchall()
        if not trace or not sample:
            return result
        original = json.loads(sample['payload'])
        trace_value = json.loads(trace['payload'])
        # Request records contain the exact messages and wire parameters sent
        # to the model. Surface a safe, compact snapshot alongside the batch
        # trace so the UI can distinguish system instructions from task text.
        if request_rows:
            request_payloads = [json.loads(row['payload']) for row in request_rows]
            matches = [_match_request(payload, original) for payload in request_payloads]
            # Prefer the request whose user message mentions this sample's
            # question or context. For a batch trace, several requests can be
            # equally valid; retain one real wire snapshot and label it as a
            # batch snapshot in the response rather than showing the first
            # request as if it belonged exclusively to this sample.
            preferred_roles = ({'answer_generation', 'generation'}
                               if trace_value.get('scope') == 'answer'
                               else {'generation'})
            preferred_matches = [value for value in matches if value['role'] in preferred_roles] or matches
            selected = max(preferred_matches, key=lambda value: (
                value['score'], value['input_match'], value['context_matches'],
                value['role'] in {'generation', 'answer_generation'},
                -(int(value['sequence']) if str(value['sequence']).isdigit() else 0),
            ))
            request_record = selected['request']
            messages = selected['messages']
            system_prompt = next((message.get('content') for message in messages
                                  if isinstance(message, dict) and message.get('role') == 'system'
                                  and isinstance(message.get('content'), str)), None)
            safe_messages = [{'role': message.get('role'), 'content': message.get('content')}
                             for message in messages
                             if isinstance(message, dict) and message.get('role') in {'system', 'user', 'assistant'}
                             and isinstance(message.get('content'), str)]
            # Older RAGAS retrieval traces put the static instructions in the
            # user message. Reconstruct the intended role split for display
            # while keeping the immutable request record unchanged.
            if not system_prompt and trace_value.get('backend') in {'ragas', 'ragas_native'}:
                user_index = next((index for index, message in enumerate(safe_messages) if message['role'] == 'user'), None)
                if user_index is not None:
                    user_content = safe_messages[user_index]['content']
                    context_marker = '\nContext:'
                    if context_marker in user_content:
                        system_prompt, context = user_content.split(context_marker, 1)
                        safe_messages[user_index]['content'] = 'Context:' + context
                    elif trace_value.get('prompt'):
                        system_prompt = str(trace_value['prompt'])
                system_prompt = system_prompt.strip() if isinstance(system_prompt, str) else system_prompt
            # Present the current question-length policy for legacy traces that
            # were recorded before the concise 5–20 character constraint.
            if isinstance(system_prompt, str):
                system_prompt = (system_prompt.replace('15–70', '5–20')
                                 .replace('15-70', '5-20')
                                 .replace('15 and 70', '5 and 20'))
            parameters = {key: value for key, value in request_record.items()
                          if key not in {'model', 'messages', 'stream', 'stream_options'}}
            # Keep the common controls visible even when the provider accepts
            # its own defaults and therefore the field was omitted from the
            # wire payload. A null value means "service default" in the UI.
            parameter_details = {key: {'value': value, 'source': 'request'}
                                 for key, value in parameters.items()}
            for key in ('max_tokens', 'temperature', 'top_p', 'thinking',
                        'enable_thinking', 'thinking_budget', 'reasoning_effort',
                        'chat_template_kwargs'):
                parameter_details.setdefault(key, {'value': None, 'source': 'service_default'})
            trace_value['llm_request'] = {
                'model': request_record.get('model'),
                'system_prompt': system_prompt,
                'messages': safe_messages,
                'parameters': parameters,
                'parameter_details': parameter_details,
                # ``scope`` comes from the immutable trace snapshot. Dataset
                # generation is batch-scoped, while answer regeneration has
                # one request per sample. The match fields make the distinction
                # visible to the UI and help diagnose old records.
                'request_scope': 'sample' if trace_value.get('scope') == 'answer' else 'batch',
                'request_match': ('sample_input' if selected['input_match'] else
                                  'sample_context' if selected['context_matches'] else 'batch_snapshot'),
                'request_match_score': selected['score'],
                'request_context_matches': selected['context_matches'],
                'request_context_total': selected['context_total'],
                'request_sequence': selected['sequence'],
                'request_role': selected['role'],
            }
            generator = (trace_value.get('models') or {}).get('generator') or {}
            selection = generator.get('selection') if isinstance(generator, dict) else None
            # Traces written before provider selection was copied into the
            # model snapshot still contain the exact selection in generation
            # options. Use it for replay instead of silently choosing the
            # first configured Playground provider.
            if not isinstance(selection, dict):
                options_selection = (trace_value.get('options') or {}).get('llm')
                if isinstance(options_selection, dict):
                    selection = options_selection
            if isinstance(selection, dict):
                trace_value['llm_request'].update(
                    provider_id=selection.get('id'),
                    credential_id=selection.get('credential_id'),
                )
            # Keep every request in execution order so the detail view can
            # explain multi-step generation flows (for example generation →
            # quality review → rewrite), instead of showing one arbitrary
            # request from a batch.
            trace_value['llm_requests'] = [{
                'sequence': match['sequence'],
                'role': match['role'],
                'model': match['request'].get('model'),
                'messages': match['messages'],
                'parameters': {key: value for key, value in match['request'].items()
                               if key not in {'model', 'messages', 'stream', 'stream_options'}},
            } for match in sorted(matches, key=lambda value: (int(value['sequence']) if str(value['sequence']).isdigit() else 0, value['role']))]
        return {'availability': 'recorded', 'sample': original,
                'edited': any(item.get(key) != original.get(key) for key in ('input', 'expected_output', 'context')),
                'trace': trace_value}

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
