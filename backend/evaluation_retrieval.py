"""Bounded Dify fan-out, provenance-preserving merge and global reranking."""
from concurrent.futures import ThreadPoolExecutor
import math
import time

import requests

from backend.evaluation_contracts import safe_evidence
from backend.evaluation_execution import EvaluationFailure, call_target


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def reranker_parameters(config):
    # vLLM rejects an entire rerank batch when just one query/document pair
    # exceeds max_model_len. Let its own tokenizer enforce the model limit;
    # character slicing cannot reliably enforce a token budget (especially CJK).
    return {'truncate_prompt_tokens': -1} if config.get('provider') == 'vllm' else {}


def _reranker_rejection(response):
    # Inspect only to select a safe code; never persist upstream messages, which
    # may contain credentials, source documents or private endpoints.
    try:
        data = response.json()
    except ValueError:
        data = None
    error = data.get('error', data) if isinstance(data, dict) else None
    message = str(error.get('message', error.get('detail', ''))).lower() if isinstance(error, dict) else ''
    too_long = ('token' in message or 'length' in message) and any(
        marker in message for marker in ('maximum', 'too long', 'exceed', 'max_model_len', 'context length'))
    return EvaluationFailure('reranker_input_too_long' if too_long else 'reranker_request_rejected', True)


def rerank_candidates(query, candidates, target, reranker, tracker):
    config, key = reranker
    if sum(len(c['content']) for c in candidates) > 2_000_000:
        raise EvaluationFailure('response_too_large', True)
    def request():
        response = requests.post(config['base_url'],
            headers={'Authorization': 'Bearer '+key} if key else {},
            json={'model': config['model'], 'query': query,
                  'documents': [c['content'] for c in candidates], 'return_documents': False,
                  'top_n': min(target['retrieval_top_k'], len(candidates)),
                  **reranker_parameters(config)},
            timeout=(5, target['timeout_seconds']), allow_redirects=False)
        if response.status_code in {400, 404, 422}:
            raise _reranker_rejection(response)
        response.raise_for_status()
        if not 200 <= response.status_code < 300:
            raise EvaluationFailure('unexpected_redirect', True)
        if len(response.content) > 2_000_000:
            raise EvaluationFailure('response_too_large', True)
        data = response.json()
        if not isinstance(data, dict):
            raise EvaluationFailure('invalid_target_response', True)
        output = data.get('output') if isinstance(data.get('output'), dict) else data
        rows = output.get('results')
        if not isinstance(rows, list) or not rows:
            raise EvaluationFailure('invalid_target_response', True)
        ranked, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise EvaluationFailure('invalid_target_response', True)
            index, score = row.get('index'), row.get('relevance_score', row.get('score'))
            if type(index) is not int or not 0 <= index < len(candidates) or index in seen or not _number(score):
                raise EvaluationFailure('invalid_target_response', True)
            seen.add(index)
            ranked.append({**candidates[index], 'score': score})
        tracker.usage('reranker', data.get('usage') or {})
        return sorted(ranked, key=lambda c: c['score'], reverse=True)
    return tracker.call('reranker', 0, request)


def retrieve_many(sample, target, routes, reranker, tracker):
    started = time.perf_counter()
    def retrieve(index):
        snapshot, config, key = routes[index]
        observation = call_target(sample, target, config, key, tracker)
        contexts = observation.get('retrieval_context')
        if not isinstance(contexts, list):
            raise EvaluationFailure('invalid_target_response', True)
        sources = observation['metadata']['sources']
        return [{**source, 'content': content,
                 'score': source.get('score') if _number(source.get('score')) else None,
                 'config_id': snapshot['selection']['id'],
                 'knowledge_base_name': snapshot.get('name'),
                 'external_dify_dataset_id': snapshot.get('external_dify_dataset_id'),
                 'base_identity': (snapshot.get('base_url'), snapshot.get('external_dify_dataset_id')),
                 'retrieval_score': source.get('score') if _number(source.get('score')) else None}
                for content, source in zip(contexts[:target['retrieval_candidate_top_k']], sources)]

    # Results retain configured route order regardless of request completion order.
    # A failed route fails this sample: partial retrieval must not receive a score.
    with ThreadPoolExecutor(max_workers=min(4, len(routes))) as executor:
        batches = list(executor.map(retrieve, range(len(routes))))
    candidates, seen = [], set()
    for batch in batches:
        for candidate in batch:
            identity = (candidate['base_identity'], candidate.get('chunk_id') or candidate['content'])
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(candidate)
    ranked = rerank_candidates(sample['input'], candidates, target, reranker, tracker) if reranker and candidates else candidates
    if not reranker:
        ranked.sort(key=lambda c: c['score'] if _number(c.get('score')) else -math.inf, reverse=True)
    threshold = target.get('retrieval_score_threshold') or 0
    selected = [c for c in ranked if (_number(c.get('score')) and c['score'] >= threshold)
                or (not reranker and threshold == 0 and not _number(c.get('score')))][:target['retrieval_top_k']]
    ids = [str(c['chunk_id']) for c in selected if c.get('chunk_id')]
    if sum(len(c['content']) for c in selected) > 1_000_000:
        raise EvaluationFailure('response_too_large', True)
    sources = [{k: v for k, v in c.items() if k not in {'content', 'base_identity'}} for c in selected]
    return {'actual_output': None, 'retrieval_context': [c['content'] for c in selected],
            'retrieved_chunk_ids': ids if len(ids) == len(selected) else None,
            'duration_ms': round((time.perf_counter()-started)*1000, 2),
            'usage': tracker.stage_usage.copy(),
            'metadata': {'sources': safe_evidence(sources), 'retrieval_strategy': {
                'mode': 'reranker' if reranker else 'single_source',
                'route_count': len(routes), 'candidate_count': len(candidates),
                'candidate_top_k': target['retrieval_candidate_top_k'],
                'top_k': target['retrieval_top_k'], 'score_threshold': threshold,
                'reranker_parameters': reranker_parameters(reranker[0]) if reranker else {},
                 'routes': [{'config_id': route[0]['selection']['id']} for route in routes]}}}
