"""Quality evaluation configuration, queue controls and versioned results."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from typing import Literal
from backend.evaluation_results import summarize, export_run

from backend.evaluation_contracts import (DEFAULT_JUDGE_PROMPT, METRICS, RUN_STATES, CASE_STATES, METRIC_STATES, PrepareEvaluation,
    metric_catalog, model_evidence, scoring_engine, evaluation_type)
from backend.evaluation_store import EvaluationStore, fingerprint
from backend.evaluation_recall import recall_settings
from backend.schemas import PlaygroundRequest
from backend.dataset_llm import generation_parameters
from backend.tenant_context import can_manage_workspace


def install_evaluation_routes(app, store, resolve_access, concrete_store):
    router = APIRouter(prefix='/api', tags=['evaluations'])

    def public(value):
        if isinstance(value, dict):
            return {key: public(item) for key, item in value.items() if key != 'worker_token'}
        if isinstance(value, list):
            return [public(item) for item in value]
        return value

    def repo():
        return EvaluationStore(concrete_store())

    @router.get('/evaluation-capabilities')
    def capabilities():
        return {'contract_version': '2.0', 'execution_available': True,
                'evaluation_types': ['rag', 'retrieval'],
                'scoring_engines': [scoring_engine(name) for name in ('deepeval', 'ragas', 'rules')],
                'metrics': metric_catalog(), 'states': {'run': RUN_STATES, 'case': CASE_STATES, 'metric': METRIC_STATES}}

    @router.post('/evaluation-runs', status_code=201)
    def prepare(payload: PrepareEvaluation):
        target_repo = repo()  # Capture workspace storage before doing any work.
        defaults = store.system_settings()
        budget_values = payload.budget.model_dump()
        if 'max_requests' not in payload.budget.model_fields_set:
            budget_values['max_requests'] = defaults.get('evaluation_max_requests', 500)
        if 'max_output_tokens' not in payload.budget.model_fields_set:
            budget_values['max_output_tokens'] = defaults.get('evaluation_max_output_tokens', 1000000)
        # Concurrency is a workspace policy, frozen into the run evidence so
        # changing the setting never changes an evaluation already in flight.
        budget_values['evaluation_max_concurrent'] = defaults.get('evaluation_max_concurrent', 3)
        request_values = payload.model_dump(mode='json', exclude={'evaluation_type'} if payload.evaluation_type is None else set())
        request_values['budget'] = budget_values
        request_hash = fingerprint(request_values)
        existing = target_repo.existing(payload.idempotency_key, request_hash)
        if existing:
            return public(existing)
        engine = payload.scoring_engine or defaults.get('evaluation_scoring_engine', 'deepeval')
        if any(METRICS[metric.id]['engine'] not in {engine, 'rules'} for metric in payload.metrics):
            raise HTTPException(400, '指标必须属于所选评分引擎')
        judge = payload.judge.model_dump() if payload.judge else defaults.get('evaluation_default_judge')
        needs_judge = any(METRICS[m.id]['engine'] != 'rules' for m in payload.metrics)
        if needs_judge and not judge:
            raise HTTPException(400, '请选择独立的结果评分模型，或在空间设置中配置')

        def resolve(selection, benchmark, *, scoring=False):
            config, _key = resolve_access(PlaygroundRequest(benchmark=benchmark, provider_id=selection['id'],
                        model=selection.get('model'), credential_id=selection.get('credential_id'), query='evaluation preparation',
                        documents=['configuration validation'] if benchmark == 'reranker' else []))
            snapshot = model_evidence(config, selection, benchmark)
            if scoring:
                snapshot['inference_parameters'] = generation_parameters(config, _key, budget_values['judge_max_tokens'])
            return snapshot

        if payload.sample_kinds != ['single_turn']:
            raise HTTPException(400, '当前评测仅支持单轮样本')
        target = resolve(payload.target.selection.model_dump(), payload.target.benchmark)
        primary = target.copy()
        # A single Dify knowledge base should use Dify's own current retrieval
        # settings. PrismLab's fusion controls are only meaningful once there
        # are multiple routes (or an explicit local override is supplied).
        multi_route = bool(len(payload.target.selections) > 1 or payload.target.retrieval_reranker or payload.target.retrieval_score_threshold is not None)
        target.update(**payload.target.model_dump(exclude={'selection','benchmark','selections','retrieval_reranker'}))
        if multi_route:
            selections = payload.target.selections or [payload.target.selection]
            bases = [primary] + [resolve(s.model_dump(), 'dify-retrieve') for s in selections[1:]]
            identities = {(b.get('base_url'), b.get('external_dify_dataset_id')) for b in bases}
            if len(identities) != len(bases):
                raise HTTPException(400, '所选配置包含重复的 Dify 知识库，请只保留一项')
            reranker = (payload.target.retrieval_reranker.model_dump() if payload.target.retrieval_reranker else None) if 'retrieval_reranker' in payload.target.model_fields_set else defaults.get('evaluation_default_reranker')
            if len(bases) > 1 and not reranker:
                raise HTTPException(400, '多路召回需要选择重排模型，或在空间设置中配置默认重排模型')
            target['knowledge_bases'] = bases
            target['retrieval_reranker'] = resolve(reranker, 'reranker') if reranker else None
        judge_snapshot = resolve(judge, 'llm', scoring=True) if needs_judge else None
        if judge_snapshot:
            judge_snapshot['prompt'] = payload.judge_prompt or defaults.get('evaluation_judge_prompt') or DEFAULT_JUDGE_PROMPT
        selected = {metric['id']: {k:v for k,v in metric.items() if k != 'evaluation_types'} for metric in metric_catalog()}
        if any(not any(k in selected[m.id]['sample_kinds'] for k in payload.sample_kinds) for m in payload.metrics):raise HTTPException(400,'指标不支持所选样本类型')
        metrics = [{**selected[metric.id], **metric.model_dump(exclude={'id','threshold'}), 'threshold': {**selected[metric.id]['threshold'], 'value': metric.threshold}}
                   for metric in payload.metrics]
        cutoff = recall_settings(target, metrics, resolve_access)
        if cutoff:
            for metric in metrics:
                if metric['id'] == 'rules.recall_at_k':
                    metric.update(cutoff)
        return public(target_repo.prepare(payload, request_hash, {'target': target, 'result_judge': judge_snapshot,
                                  'scoring_engine': scoring_engine(engine), 'metrics': metrics, 'budget': budget_values,
                                  'evaluation_type':payload.evaluation_type or evaluation_type({'target':target}),
                                  'sample_kinds':payload.sample_kinds, 'template': payload.template, 'use_cache': payload.use_cache}))

    @router.post('/evaluation-runs/{run_id}/start', status_code=202)
    def start(run_id: str):
        return public(repo().enqueue(run_id))

    @router.post('/evaluation-runs/{run_id}/rerun', status_code=201)
    def rerun(run_id: str):
        target_repo = repo()
        original = target_repo.get(run_id)
        cutoff = recall_settings(original['evidence']['target'], original['evidence']['metrics'], resolve_access)
        return public(target_repo.rerun(run_id, recall_cutoff=cutoff))

    @router.get('/evaluation-runs')
    def list_runs(limit: int = Query(50, ge=1, le=100), dataset_id: str | None = Query(None, min_length=1, max_length=128)):
        return {'items': [{k: v for k, v in item.items() if k != 'worker_token'} for item in repo().list(limit, dataset_id=dataset_id)]}

    @router.get('/evaluation-runs/{run_id}')
    def get_run(run_id: str):
        value = repo().get(run_id)
        value['summary'] = summarize(value)
        return public(value)

    @router.delete('/evaluation-runs/{run_id}')
    def delete_run(run_id: str):
        if not can_manage_workspace():
            raise HTTPException(403, '仅空间所有者或管理员可删除评测记录')
        return repo().delete(run_id)

    @router.get('/evaluation-runs/{run_id}/export')
    def export(run_id: str, format: Literal['json','jsonl','csv'] = 'json'):
        value = public(repo().get(run_id))
        version = repo().version(value['evidence']['dataset_version_id'])
        return Response(export_run(value,version,format),media_type='text/csv' if format=='csv' else 'application/json',
                        headers={'Content-Disposition':f'attachment; filename="evaluation-{run_id}.{format}"'})

    @router.get('/dataset-versions/{version_id}')
    def get_version(version_id: str):
        return repo().version(version_id)

    @router.post('/evaluation-runs/{run_id}/cancel')
    def cancel(run_id: str):
        return public(repo().cancel(run_id))

    @router.post('/evaluation-runs/{run_id}/retry-failed')
    def retry(run_id: str):
        return public(repo().retry_failed(run_id))

    app.include_router(router)
