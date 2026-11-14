"""Actual target calls and official scoring adapters, sharing workspace budgets."""
import asyncio
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests
from fastapi import HTTPException

os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
os.environ['DEEPEVAL_TELEMETRY_OPT_OUT'] = '1'

from backend.evaluation_contracts import model_evidence, safe_evidence
from backend.evaluation_store import fingerprint
from backend.dataset_llm import generation_parameters
from backend.schemas import PlaygroundRequest
from shared.llm_stream import stream_chat

PERMANENT = {400: 'invalid_request', 401: 'authentication_failed', 402: 'quota_exhausted',
             403: 'authentication_failed', 404: 'invalid_request', 422: 'invalid_request'}


class EvaluationFailure(Exception):
    def __init__(self, code, permanent=False):
        super().__init__(code)
        self.code, self.permanent = code, permanent


def classified(error):
    if isinstance(error, EvaluationFailure):
        return error
    if isinstance(error, HTTPException):
        if error.status_code == 409:
            return EvaluationFailure('stopped', True)
        if error.status_code == 402:
            return EvaluationFailure('budget_exhausted', True)
    response = getattr(error, 'response', None)
    status = response.status_code if response is not None else None
    if status in PERMANENT:
        return EvaluationFailure(PERMANENT[status], True)
    return EvaluationFailure('service_unavailable', False)


class RequestTracker:
    def __init__(self, repo, run_id, token, budget, judge_parameters=None):
        self.repo, self.run_id, self.token, self.budget = repo, run_id, token, budget
        self.explanations = []
        self.stage_usage = {'requests': 0, 'input_tokens': None, 'output_tokens': None, 'cost': None}
        self.lock = threading.Lock()
        self.judge_parameters = judge_parameters

    def call(self, stage, output_limit, callback):
        for attempt in range(self.budget['max_retries']+1):
            try:
                self.repo.reserve_request(self.run_id, self.token, stage, output_limit)
                with self.lock:
                    self.stage_usage['requests'] += 1
                return callback()
            except Exception as error:
                failure = classified(error)
                if failure.permanent or attempt == self.budget['max_retries']:
                    raise failure from None
                # Short bounded backoff; lease and cancellation checked before retry.
                time.sleep(min(0.25*2**attempt, 2))

    def usage(self, stage, value):
        parsed = {'input_tokens': value.get('prompt_tokens', value.get('input_tokens')),
                  'output_tokens': value.get('completion_tokens', value.get('output_tokens'))}
        self.repo.add_usage(self.run_id, self.token, stage, parsed)
        with self.lock:
            for key, count in parsed.items():
                if type(count) is int and count >= 0:
                    self.stage_usage[key] = (self.stage_usage[key] or 0)+count

    def judge_text(self, config, key, prompt, instructions):
        parameters = self.judge_parameters if self.judge_parameters is not None else generation_parameters(
            config, key, self.budget['judge_max_tokens'])
        limit = parameters['max_tokens']
        self.stage_usage['inference_parameters'] = parameters.copy()
        def request():
            sample, content = stream_chat(config['base_url'], config['model'], key, prompt,
                max_tokens=limit, timeout=180, capture=True, capture_reasoning=False,
                require_complete=True, system_prompt=instructions, request_parameters=parameters)
            self.usage('judge', sample)
            try:
                parsed = json.loads(content.strip().removeprefix('```json').removesuffix('```').strip())
                self.explanations.append(parsed)
            except ValueError:
                pass
            return content
        return self.call('judge', limit, request)


def call_target(sample, target, config, key, tracker, conversation_id=""):
    if target['benchmark'] not in {'llm', 'dify-chat', 'dify-retrieve'}:
        raise EvaluationFailure('unsupported_target', True)
    headers = {'Authorization': 'Bearer '+key} if key else {}
    started = time.perf_counter()
    def request():
        benchmark = target['benchmark']
        if benchmark == 'llm':
            # Use the established streaming transport, requiring a complete answer.
            usage, answer = stream_chat(config['base_url'], config['model'], key, sample['input'],
                max_tokens=target['max_tokens'], timeout=target['timeout_seconds'], capture=True,
                capture_reasoning=False, require_complete=True, system_prompt=target['prompt'])
            tracker.usage('target', usage)
            return {'actual_output': answer, 'retrieval_context': None, 'retrieved_chunk_ids': None,
                    'metadata': {}, 'usage': tracker.stage_usage.copy()}
        root = config['base_url'].rstrip('/')
        if not root.endswith('/v1'):
            root += '/v1'
        if benchmark == 'dify-retrieve':
            endpoint = root+'/datasets/'+quote(config['dataset_id'], safe='')+'/retrieve'
            # Let Dify apply the knowledge base's current retrieval settings.
            # Evaluation-specific top-k/threshold controls are applied only to
            # the merged results in evaluation_retrieval.py, never sent here.
            payload = {'query': sample['input']}
        else:
            endpoint = root+'/chat-messages'
            payload = {'inputs': {}, 'query': sample['input'], 'response_mode': 'blocking',
                       'conversation_id': conversation_id, 'auto_generate_name': False, 'user': 'prismlab-evaluation'}
        response = requests.post(endpoint, json=payload, headers=headers, timeout=(5,target['timeout_seconds']), allow_redirects=False)
        if benchmark == 'dify-retrieve' and response.status_code in {400, 422}:
            raise EvaluationFailure('dify_retrieval_rejected', True)
        response.raise_for_status()
        if not 200<=response.status_code<300:raise EvaluationFailure('unexpected_redirect',True)
        if len(response.content) > 2_000_000:
            raise EvaluationFailure('invalid_request', True)
        data = response.json()
        answer = None if benchmark == 'dify-retrieve' else data.get('answer')
        metadata = data.get('metadata') or {}
        records = data.get('records') if benchmark == 'dify-retrieve' else metadata.get('retriever_resources')
        contexts, ids, sources = None, None, []
        if isinstance(records, list):
            contexts, ids = [], []
            for record in records:
                segment = record.get('segment') or record
                content = segment.get('content')
                chunk_id = segment.get('id') or record.get('segment_id')
                if isinstance(content, str) and content.strip():
                    contexts.append(content)
                    if chunk_id:
                        ids.append(str(chunk_id))
                    sources.append({'chunk_id': chunk_id, 'document_id': segment.get('document_id') or record.get('document_id'),
                                    'document_name': record.get('document_name'), 'score': record.get('score')})
            if len(ids) != len(contexts):
                ids = None  # Missing stable IDs cannot be fabricated from order/text.
        usage = metadata.get('usage') or {}
        tracker.usage('target', usage)
        return {'actual_output': answer, 'retrieval_context': contexts, 'retrieved_chunk_ids': ids,
                'metadata': {'sources': sources, 'conversation_id': data.get('conversation_id')},
                'usage': tracker.stage_usage.copy()}
    result = tracker.call('target', 0 if target['benchmark'] == 'dify-retrieve' else target['max_tokens'], request)
    result['duration_ms'] = round((time.perf_counter()-started)*1000, 2)
    return result


def ragas_llm(config, key, request_text):
    from ragas.llms.base import BaseRagasLLM
    from ragas.run_config import RunConfig
    from langchain_core.outputs import Generation, LLMResult
    class WorkspaceRagasLLM(BaseRagasLLM):
        def generate_text(self, prompt, n=1, temperature=0.01, stop=None, callbacks=None):
            return LLMResult(generations=[[Generation(text=request_text(prompt.to_string())) for _ in range(n)]])
        async def agenerate_text(self, *args, **kwargs):
            return await asyncio.to_thread(self.generate_text, *args, **kwargs)
        def is_finished(self, response):
            return True
    return WorkspaceRagasLLM(run_config=RunConfig(max_retries=1, max_wait=1, timeout=180))


def score_metric(metric, sample, observation, judge, config, key, tracker):
    metric_id = metric['id']
    if metric['engine'] == 'rules':
        return score_rule(metric, sample, observation)
    if metric['engine'] == 'ragas':
        from ragas.dataset_schema import SingleTurnSample
        from ragas.metrics import (Faithfulness, LLMContextRecall, LLMContextPrecisionWithReference,
                                   LLMContextPrecisionWithoutReference, FactualCorrectness)
        classes = {'ragas.faithfulness': Faithfulness, 'ragas.context_recall': LLMContextRecall,
                   'ragas.context_precision': LLMContextPrecisionWithReference,
                   'ragas.context_precision_without_reference': LLMContextPrecisionWithoutReference,
                   'ragas.answer_accuracy': FactualCorrectness}
        model = ragas_llm(config, key, lambda prompt: tracker.judge_text(config, key, prompt, judge['prompt']))
        sdk_metric = classes[metric_id](llm=model)
        case = SingleTurnSample(user_input=sample['input'], response=observation.get('actual_output'),
                                reference=sample.get('expected_output'), retrieved_contexts=observation.get('retrieval_context'))
        score = asyncio.run(sdk_metric.single_turn_ascore(case))
        reason = json.dumps(tracker.explanations, ensure_ascii=False)[:20000]
        return float(score), reason or f'{sdk_metric.name}: {score}'
    from deepeval.models import DeepEvalBaseLLM
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval, ContextualPrecisionMetric, ContextualRecallMetric
    from deepeval.test_case import LLMTestCase, SingleTurnParams
    class JudgeLLM(DeepEvalBaseLLM):
        def load_model(self): return config
        def get_model_name(self): return config['model']
        def generate(self, prompt, schema=None):
            if schema:
                prompt += '\nReturn JSON matching this schema:\n'+json.dumps(schema.model_json_schema())
            content = tracker.judge_text(config, key, prompt, judge['prompt'])
            if schema:
                return schema.model_validate_json(content.strip().removeprefix('```json').removesuffix('```').strip())
            return content
        def supports_multimodal(self):return False
        async def a_generate(self, *args, **kwargs):
            return await asyncio.to_thread(self.generate, *args, **kwargs)
    model = JudgeLLM(model=config['model'])
    if metric_id in {'deepeval.reference_correctness','deepeval.custom'}:
        if metric_id == 'deepeval.reference_correctness':
            criteria = ('Evaluate factual correctness and completeness of the actual answer against the reference answer for the input. '
                        'Accept semantically equivalent wording. Penalize contradictions, invented facts and missing essential facts. '
                        'Do not use exact string matching or penalize harmless extra grounded detail.')
            params = [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT]
        else:
            criteria = metric['criteria']+'\nRubric examples:\n'+metric.get('examples','')
            params = [SingleTurnParams.INPUT,SingleTurnParams.ACTUAL_OUTPUT]
        sdk_metric = GEval(name=metric_id, criteria=criteria, evaluation_params=params, model=model,
                           threshold=metric['threshold']['value'], async_mode=False)
    else:
        cls = {'deepeval.answer_relevancy': AnswerRelevancyMetric, 'deepeval.faithfulness': FaithfulnessMetric,
               'deepeval.context_precision': ContextualPrecisionMetric, 'deepeval.context_recall': ContextualRecallMetric}[metric_id]
        sdk_metric = cls(model=model, threshold=metric['threshold']['value'], async_mode=False, include_reason=True)
    case = LLMTestCase(input=sample['input'], actual_output=observation.get('actual_output') or '',
                       expected_output=sample.get('expected_output'), retrieval_context=observation.get('retrieval_context'))
    sdk_metric.measure(case, _show_indicator=False)
    return float(sdk_metric.score), sdk_metric.reason


def score_rule(metric, sample, observation):
    annotations = sample.get('annotations', {})
    actual = observation.get('actual_output') or ''
    if metric['id'] == 'rules.required_text':
        missing = [text for text in metric['required_text'] if text.casefold() not in actual.casefold()]
        return (0.0 if missing else 1.0), 'Missing required phrases: '+json.dumps(missing, ensure_ascii=False)
    if metric['id'] == 'rules.json_schema':
        import jsonschema
        try:
            jsonschema.Draft202012Validator(metric['json_schema']).validate(json.loads(actual))
            return 1.0, 'Valid JSON matching the configured schema'
        except (ValueError,jsonschema.ValidationError):
            return 0.0, 'Answer is not JSON matching the configured schema'
    retrieved = list(dict.fromkeys(observation.get('retrieved_chunk_ids') or []))[:metric['k']]
    relevant = set(annotations.get('relevant_chunk_ids', []))
    if metric['id'] == 'rules.recall_at_k':
        return len(relevant.intersection(retrieved))/len(relevant), f"Recall@{metric['k']}: {len(relevant.intersection(retrieved))}/{len(relevant)} annotated relevant chunks retrieved (returned {len(observation.get('retrieved_chunk_ids') or [])} chunks)"
    if metric['id'] == 'rules.mrr':
        rank = next((i+1 for i,v in enumerate(retrieved) if v in relevant), None)
        return (1/rank if rank else 0.0), f'First relevant rank: {rank}'
    grades = annotations['graded_relevance']
    gains = lambda values: sum((2**float(v)-1)/math.log2(i+2) for i,v in enumerate(values))
    ideal = gains(sorted(grades.values(),reverse=True)[:metric['k']])
    return (gains([grades.get(v,0) for v in retrieved])/ideal if ideal else 0.0), 'nDCG computed from explicitly annotated graded relevance'


def run_evaluation(repo, claim, resolve_access):
    run_id, token = claim['id'], claim['worker_token']
    stopped = threading.Event()
    cancelled = threading.Event()
    repository_lock = threading.RLock()

    class SynchronizedRepository:
        """Serialize payload read/modify/write transactions across sample workers."""
        def __getattr__(self, name):
            method = getattr(repo, name)
            if not callable(method):
                return method
            def call(*args, **kwargs):
                with repository_lock:
                    return method(*args, **kwargs)
            return call

    worker_repo = SynchronizedRepository()

    def renew():
        while not stopped.wait(10):
            try: worker_repo.heartbeat(run_id, token)
            except HTTPException: return
    heartbeat = threading.Thread(target=renew,daemon=True)
    heartbeat.start()
    try:
        run = worker_repo.get(run_id)
        evidence = run['evidence']
        def resolve(snapshot):
            selection = snapshot['selection']
            if snapshot['benchmark'] not in {'llm', 'dify-chat', 'dify-retrieve', 'reranker'}:
                raise EvaluationFailure('unsupported_target', True)
            config, key = resolve_access(PlaygroundRequest(benchmark=snapshot['benchmark'], provider_id=selection['id'],
                model=selection.get('model'), credential_id=selection.get('credential_id'), query='evaluation',
                documents=['configuration validation'] if snapshot['benchmark'] == 'reranker' else []))
            current = model_evidence(config, selection, snapshot['benchmark'])
            if any(current.get(field) != snapshot.get(field) for field in current):
                raise EvaluationFailure('configuration_changed', True)
            return config, key
        target_config, target_key = resolve(evidence['target'])
        routes = [(base, *resolve(base)) for base in evidence['target'].get('knowledge_bases', [])]
        reranker_snapshot = evidence['target'].get('retrieval_reranker')
        reranker = resolve(reranker_snapshot) if reranker_snapshot else None
        judge_config, judge_key = resolve(evidence['result_judge']) if evidence.get('result_judge') else (None,None)
        judge_parameters = (evidence['result_judge'].get('inference_parameters') or generation_parameters(
            judge_config, judge_key, evidence['budget']['judge_max_tokens'])) if judge_config else None
        version = worker_repo.version(evidence['dataset_version_id'])
        samples = {s['id']: s for s in version['samples']}
        def process_case(case):
            if cancelled.is_set():
                return
            worker_repo.heartbeat(run_id, token)
            sample = samples[case['sample_id']]
            if case['status'] == 'pending':
                tracker = RequestTracker(worker_repo,run_id,token,evidence['budget'])
                try:
                    if evidence['target']['benchmark'] == 'dify-retrieve':
                        if routes:
                            from backend.evaluation_retrieval import retrieve_many
                            observation = retrieve_many(sample, evidence['target'], routes, reranker, tracker)
                        if not routes:
                            observation = call_target(sample,evidence['target'],target_config,target_key,tracker)
                    else:
                        observation = call_target(sample,evidence['target'],target_config,target_key,tracker)
                    worker_repo.record_target(run_id,case['id'],token,observation=observation)
                except Exception as error:
                    failure = classified(error)
                    if failure.code == 'stopped': return
                    # A retrieval target is shared by every sample.  If the
                    # Dify gateway is unavailable, retrying each case merely
                    # burns the evaluation budget and produces a page full of
                    # identical "application call failed" rows.  Abort once
                    # and expose the actionable service-unavailable status.
                    if evidence['target']['benchmark'] == 'dify-retrieve' and failure.code == 'service_unavailable':
                        raise failure
                    if failure.permanent:
                        raise failure
                    worker_repo.record_target(run_id,case['id'],token,error=True)
            current = worker_repo.get(run_id)
            if current['status'] != 'running': return
            case = next(c for c in current['cases'] if c['id'] == case['id'])
            if case['status'] != 'completed': return
            for result in current['metric_results']:
                if result['case_id'] != case['id'] or result['status'] != 'pending': continue
                metric = next(m for m in evidence['metrics'] if m['id']==result['metric_id'])
                cache_key = fingerprint({'adapter':'3', 'metric':metric, 'sample': {k:sample.get(k) for k in ('input','expected_output','annotations')},
                                         'observation': {k:case['observation'].get(k) for k in ('actual_output','retrieval_context','retrieved_chunk_ids')},
                                         'target':evidence['target'], 'judge':evidence.get('result_judge'),
                                         'judge_inference_parameters':judge_parameters})
                cached = worker_repo.cache_get(cache_key) if evidence.get('use_cache') else None
                tracker = RequestTracker(worker_repo,run_id,token,evidence['budget'],judge_parameters)
                try:
                    if cached:
                        score, reason = cached['score'], cached['reason']
                    else:
                        score, reason = score_metric(metric,sample,case['observation'],evidence.get('result_judge'),judge_config,judge_key,tracker)
                    worker_repo.record_metric(run_id,result['id'],token,score=score,reason=reason,
                                       usage={**tracker.stage_usage,'cache_hit':bool(cached)})
                    if not cached and evidence.get('use_cache'):
                        worker_repo.cache_put(cache_key,{'score':score,'reason':reason})
                except Exception as error:
                    failure = classified(error)
                    if failure.code == 'stopped': return
                    if failure.permanent: raise failure
                    worker_repo.record_metric(run_id,result['id'],token,error=True)

        # Runs created before the workspace setting existed use the default
        # policy as a backwards-compatible upgrade; concurrency does not alter
        # the frozen target, samples, or scoring evidence.
        concurrency = max(1, min(10, int(evidence.get('budget', {}).get('evaluation_max_concurrent', 3))))
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='evaluation-sample') as executor:
            futures = [executor.submit(process_case, case) for case in run['cases']]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    cancelled.set()
                    for pending in futures:
                        pending.cancel()
                    raise
    except Exception as error:
        failure = classified(error)
        if failure.code != 'stopped':
            try: worker_repo.abort_pending(run_id,token,failure.code)
            except HTTPException: pass
    finally:
        stopped.set()
        heartbeat.join(timeout=1)
