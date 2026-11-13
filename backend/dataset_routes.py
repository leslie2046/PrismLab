import csv
import hashlib
import io
import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from backend.bulk import BulkIds, ItemBulk
from backend.dataset_trace import DatasetTraceStore
from backend.dataset_build_control import build_state
from backend.dataset_store import DatasetStore
from backend.dataset_sources import knowledge_ids, tag_knowledge, summarize_sources
from backend.datasets import (DEFAULT_CRITIC_PROMPT, DEFAULT_PROMPT, LEGACY_DEFAULT_PROMPT, MAX_UPLOAD, MAX_SOURCE_CHARS, DatasetInfo, DatasetItem, DatasetSettings,
                              DatasetGenerationConfig, GenerationOptions, build_dataset, regenerate_answers, document_chunks, fetch_dify_chunks, parse_import)
from backend.ragas_generation import RAGAS_BACKEND_VERSION
from backend.evaluation_contracts import DEFAULT_JUDGE_PROMPT, generation_engine, model_evidence
from backend.evaluation_store import EvaluationStore
from backend.store import utc_now
from backend.dataset_golden import golden_dict
from deepeval.dataset.utils import join_context, join_retrieval_context
from backend.schemas import PlaygroundRequest, ProviderSelection
from backend.model_configs import configured_models


def generation_values(payload, purpose, **kwargs):
    values = payload.model_dump(mode='json', **kwargs)
    if 'include_expected_output' not in payload.model_fields_set:
        values['include_expected_output'] = purpose != 'retrieval'
    return values


def install_dataset_routes(app, store, resolve_access, concrete_store):
    router = APIRouter(prefix='/api', tags=['datasets'])

    def repo():
        return DatasetStore(concrete_store())

    def settings():
        value = store.system_settings()
        prompt = value.get('dataset_prompt')
        if prompt == LEGACY_DEFAULT_PROMPT:
            prompt = None
        return {'max_concurrent': value.get('dataset_max_concurrent', 3),
                'evaluation_max_concurrent': value.get('evaluation_max_concurrent', 3),
                'evaluation_max_requests': value.get('evaluation_max_requests', 500),
                'evaluation_max_output_tokens': value.get('evaluation_max_output_tokens', 500000),
                'default_llm': value.get('dataset_default_llm'),
                'default_embedding_model': value.get('dataset_default_embedding_model'),
                'default_critic_llm': value.get('dataset_default_critic_llm'),
                'default_judge_llm': value.get('evaluation_default_judge'),
                'default_reranker': value.get('evaluation_default_reranker'),
                'scoring_engine': value.get('evaluation_scoring_engine', 'deepeval'),
                'judge_prompt': value.get('evaluation_judge_prompt') or DEFAULT_JUDGE_PROMPT,
                'default_judge_prompt': DEFAULT_JUDGE_PROMPT,
                'generation_engines': [generation_engine(name) for name in ('deepeval', 'ragas_native')],
                'prompt': prompt or DEFAULT_PROMPT, 'default_prompt': DEFAULT_PROMPT,
                'critic_prompt': value.get('dataset_critic_prompt') or DEFAULT_CRITIC_PROMPT,
                'default_critic_prompt': DEFAULT_CRITIC_PROMPT,
                'sdk': 'deepeval', 'sdk_version': '4.3.2', 'generation_backends': ['deepeval', 'ragas_native']}

    def llm_access(selection):
        credential_id = selection.get('credential_id')
        if credential_id == 'legacy':
            saved = store.get_service_config(selection['id'])
            models = configured_models(saved) if saved else []
            selected = next((m for m in models if m['benchmark'] == 'llm' and selection.get('model') in {m.get('alias'), m.get('name')}), None)
            if selected and not selected.get('credentials'):
                credential_id = None  # Public catalogs synthesize a credential for old endpoint-only records.
        return resolve_access(PlaygroundRequest(benchmark='llm', provider_id=selection['id'],
                              model=selection.get('model'), credential_id=credential_id, query='generate dataset'))

    def resolve_llm(selection):
        if not selection:
            raise HTTPException(400, '请先在空间设置中选择构建数据集的默认 LLM，或在此处选择模型')
        selection = ProviderSelection.model_validate(selection).model_dump()
        config, key = llm_access(selection)
        models = [model for model in configured_models(config) if model['benchmark'] == 'llm']
        model = next((model for model in models if selection.get('model') in {model.get('alias'), model.get('name')}), None)
        model = model or (models[0] if models else None)
        credential_id = config.get('credential_id')
        if model and not model.get('credentials') and model.get('base_url'):
            credential_id = 'legacy'
        selection.update(credential_id=credential_id, model=(model.get('alias') or model['name']) if model else config['model'])
        return selection, config, key

    def resolve_embedding(selection):
        if not selection:
            raise HTTPException(400, '请选择嵌入模型，或在空间设置中配置默认嵌入模型')
        selection = ProviderSelection.model_validate(selection).model_dump()
        credential_id = selection.get('credential_id')
        if credential_id == 'legacy':
            saved = store.get_service_config(selection['id'])
            models = configured_models(saved) if saved else []
            selected = next((m for m in models if m['benchmark'] == 'embedding' and selection.get('model') in {m.get('alias'), m.get('name')}), None)
            if selected and not selected.get('credentials'):
                credential_id = None
        config, key = resolve_access(PlaygroundRequest(benchmark='embedding', provider_id=selection['id'],
            model=selection.get('model'), credential_id=credential_id, text='dataset generation'))
        models = [model for model in configured_models(config) if model['benchmark'] == 'embedding']
        model = next((model for model in models if selection.get('model') in {model.get('alias'), model.get('name')}), None)
        model = model or (models[0] if models else None)
        credential_id = config.get('credential_id')
        if model and not model.get('credentials') and model.get('base_url'):
            credential_id = 'legacy'
        selection.update(credential_id=credential_id, model=(model.get('alias') or model['name']) if model else config['model'])
        return selection, config, key

    def embedding_access(options, *, inherit_default=True):
        if options.get('generation_backend') != 'ragas_native':
            return None
        selection = options.get('embedding_model')
        if not selection and inherit_default:
            selection = settings()['default_embedding_model']
        selection, config, key = resolve_embedding(selection)
        options['embedding_model'] = selection
        return config, key

    def dify_access(config_id):
        if not config_id:
            raise HTTPException(400, '请选择 Dify 知识库配置')
        config = store.get_service_config(config_id)
        if not config or config.get('provider') != 'dify':
            raise HTTPException(400, 'Dify 配置不存在')
        resolved, key = resolve_access(PlaygroundRequest(benchmark='dify-retrieve', provider_id=config_id, query='dataset source'))
        return {**resolved, 'id': config_id, 'name': config.get('name'), 'dataset_id': config.get('dataset_id') or resolved.get('dataset_id')}, key

    async def read_upload(file):
        data = await file.read(MAX_UPLOAD + 1)
        if not data or len(data) > MAX_UPLOAD:
            raise HTTPException(400, '文件不能为空，且不能超过 10 MB')
        return data

    @router.get('/dataset-settings')
    def get_settings():
        return settings()

    @router.put('/dataset-settings')
    def save_settings(payload: DatasetSettings):
        selection = payload.default_llm.model_dump() if payload.default_llm else None
        if selection:
            selection, _, _ = resolve_llm(selection)
        updates = {}
        if 'max_concurrent' in payload.model_fields_set:
            updates['dataset_max_concurrent'] = payload.max_concurrent
        if 'evaluation_max_concurrent' in payload.model_fields_set:
            updates['evaluation_max_concurrent'] = payload.evaluation_max_concurrent
        if 'evaluation_max_requests' in payload.model_fields_set:
            updates['evaluation_max_requests'] = payload.evaluation_max_requests
        if 'evaluation_max_output_tokens' in payload.model_fields_set:
            updates['evaluation_max_output_tokens'] = payload.evaluation_max_output_tokens
        if 'default_llm' in payload.model_fields_set:
            updates['dataset_default_llm'] = selection
        if 'default_embedding_model' in payload.model_fields_set:
            embedding = payload.default_embedding_model.model_dump() if payload.default_embedding_model else None
            if embedding:
                embedding, _, _ = resolve_embedding(embedding)
            updates['dataset_default_embedding_model'] = embedding
        if 'prompt' in payload.model_fields_set:
            updates['dataset_prompt'] = payload.prompt
        if 'default_critic_llm' in payload.model_fields_set:
            critic = payload.default_critic_llm.model_dump() if payload.default_critic_llm else None
            if critic:
                critic, _, _ = resolve_llm(critic)
            updates['dataset_default_critic_llm'] = critic
        if 'critic_prompt' in payload.model_fields_set:
            updates['dataset_critic_prompt'] = payload.critic_prompt
        if 'default_judge_llm' in payload.model_fields_set:
            judge = payload.default_judge_llm.model_dump() if payload.default_judge_llm else None
            if judge:
                judge, _, _ = resolve_llm(judge)
            updates['evaluation_default_judge'] = judge
        if 'scoring_engine' in payload.model_fields_set:
            updates['evaluation_scoring_engine'] = payload.scoring_engine
        if 'default_reranker' in payload.model_fields_set:
            reranker = payload.default_reranker.model_dump() if payload.default_reranker else None
            if reranker:
                credential_id = reranker.get('credential_id')
                if credential_id == 'legacy':
                    saved = store.get_service_config(reranker['id'])
                    model = next((m for m in configured_models(saved or {}) if m['benchmark'] == 'reranker'
                                  and reranker.get('model') in {m.get('alias'), m.get('name')}), None)
                    if model and not model.get('credentials'):
                        credential_id = None
                config, _ = resolve_access(PlaygroundRequest(benchmark='reranker', provider_id=reranker['id'],
                    model=reranker.get('model'), credential_id=credential_id, query='retrieval evaluation', documents=['configuration validation']))
                reranker = model_evidence(config, reranker, 'reranker')['selection']
            updates['evaluation_default_reranker'] = reranker
        if 'judge_prompt' in payload.model_fields_set:
            updates['evaluation_judge_prompt'] = payload.judge_prompt
        store.save_system_settings(updates)
        return settings()

    @router.get('/datasets')
    def list_datasets(page: int = Query(1, ge=1, le=1000000000), page_size: int = Query(10, ge=1, le=100), q: str = Query('', max_length=200), purpose: Literal['rag', 'retrieval', 'both'] | None = None):
        result = repo().page(page, page_size, q, purpose)
        summaries = EvaluationStore(concrete_store()).latest_summaries([item['id'] for item in result['items']])
        sources = repo().source_summaries(result['items'])
        result['items'] = [{**item, 'latest_evaluation': summaries.get(item['id']), 'data_sources': sources.get(item['id'], [])} for item in result['items']]
        return result

    @router.post('/datasets', status_code=201)
    def create_dataset(payload: DatasetInfo):
        if payload.evaluation_purpose == 'retrieval':
            raise HTTPException(400, '检索评测数据集必须从 Dify 知识库生成，不能手动创建')
        return repo().create({**payload.model_dump(), 'source_type': 'manual'})

    @router.post('/datasets/import', status_code=201)
    async def import_dataset(name: str = Form(..., min_length=1, max_length=120), description: str = Form('', max_length=2000), file: UploadFile = File(...), evaluation_purpose: Literal['rag', 'retrieval', 'both'] = Form('both')):
        if evaluation_purpose == 'retrieval':
            raise HTTPException(400, '检索评测数据集必须从 Dify 知识库生成，不能导入文件')
        data = await read_upload(file)
        try:
            info = DatasetInfo(name=name, description=description, evaluation_purpose=evaluation_purpose)
            items = await run_in_threadpool(parse_import, file.filename or '', data)
        except (ValueError, RecursionError):
            # Do not expose source content embedded in parser/validation exceptions.
            raise HTTPException(400, '导入格式无效，请检查文件编码、列名和数据类型。支持 CSV、JSON、JSONL，input 必填。') from None
        target = repo()
        dataset = await run_in_threadpool(target.create, {**info.model_dump(), 'source_type': 'import', 'source_filename': file.filename})
        try:
            return await run_in_threadpool(target.add, dataset['id'], items, origin='import')
        except Exception:
            await run_in_threadpool(target.delete, dataset['id'])
            raise

    @router.post('/datasets/generate', status_code=202)
    async def generate_dataset(background_tasks: BackgroundTasks, name: str = Form(..., min_length=1, max_length=120),
                               description: str = Form('', max_length=2000), options: str = Form(..., max_length=10000),
                               files: list[UploadFile] = File(default=[]), evaluation_purpose: Literal['rag', 'retrieval', 'both'] = Form('both')):
        try:
            info = DatasetInfo(name=name, description=description, evaluation_purpose=evaluation_purpose)
            opts = GenerationOptions.model_validate_json(options)
        except ValueError:
            raise HTTPException(400, '构建参数无效，请检查大小（1–500）、质量与演化参数，以及种子数据集或生成任务') from None
        if opts.generation_backend == 'ragas':
            raise HTTPException(400, 'RAGAS 兼容生成已移除，请选择 DeepEval 或 RAGAS 官方生成 0.4.3')
        defaults = settings()
        selection, config, key = resolve_llm(opts.llm.model_dump() if opts.llm else defaults['default_llm'])
        critic = (opts.critic_llm.model_dump() if opts.critic_llm else defaults['default_critic_llm']) if opts.generation_backend == 'deepeval' else None
        critic_selection, critic_config, critic_key = resolve_llm(critic) if critic else (None, None, None)
        if opts.dify_config_id and files:
            raise HTTPException(400, '请选择文档或 Dify 其中一种数据源')
        if info.evaluation_purpose == 'retrieval' and (not opts.dify_config_id or opts.method != 'contexts' or files):
            raise HTTPException(400, '检索评测数据集只能从 Dify 知识库生成，不能手动、导入文档或按任务生成')
        dify = [dify_access(identifier) for identifier in opts.dify_config_ids] or None
        chunks = []
        total_bytes = 0
        target = repo()
        if opts.method != 'contexts' and files:
            raise HTTPException(400, '此生成方式不支持上传文档')
        if opts.method == 'goldens':
            # Reads use the current workspace store; cross-workspace IDs cannot resolve.
            seed = target.get(opts.seed_dataset_id)
            if seed['status'] == 'generating':
                raise HTTPException(409, '种子数据集正在构建，请稍后重试')
            chunks = [{'id': item['id'], 'golden': golden_dict(item),
                       'metadata': {'seed_dataset_id': opts.seed_dataset_id, 'seed_item_id': item['id']}}
                      for item in target.items(opts.seed_dataset_id, size=20000)['items'] if item['status'] == 'active']
            if not chunks:
                raise HTTPException(400, '种子数据集没有启用的数据项')
            has_context = [bool(c['golden'].get('context')) for c in chunks]
            if any(has_context) and not all(has_context):
                raise HTTPException(400, '种子项需全部包含参考上下文或全部不含上下文，请先整理种子数据集')
        elif opts.method == 'contexts' and not dify:
            if not files or len(files) > 20:
                raise HTTPException(400, '请上传 1–20 个文档')
            for index, file in enumerate(files):
                data = await read_upload(file)
                total_bytes += len(data)
                if total_bytes > MAX_UPLOAD:
                    raise HTTPException(400, '上传文件总大小不能超过 10 MB')
                try:
                    parts = await run_in_threadpool(document_chunks, file.filename or '', data)
                except Exception:
                    raise HTTPException(400, '无法解析文档。请使用 TXT、Markdown、非加密文本 PDF 或 DOCX；扫描件需先 OCR。') from None
                for part in parts:
                    part['id'] = f'file-{index + 1}-{part["id"]}'
                chunks.extend(parts)
            if sum(len(c['content']) for c in chunks) > 2_000_000:
                raise HTTPException(400, '文档总文本不能超过 200 万字符')
        generation_options = generation_values(opts, info.evaluation_purpose)
        generation_options.update(llm=selection, critic_llm=critic_selection, max_concurrent=defaults['max_concurrent'])
        embedding = embedding_access(generation_options)
        source_type = 'goldens' if opts.method == 'goldens' else 'scratch' if opts.method == 'scratch' else 'dify' if dify else 'documents'
        dataset = target.create({**info.model_dump(), **build_state(), 'build_target': opts.size, 'build_progress': 0, 'status': 'generating', 'source_type': source_type,
                                 'target_size': opts.size, 'llm': selection, 'dify_config_id': opts.dify_config_id, 'dify_config_ids': opts.dify_config_ids,
                                 'prompt': defaults['prompt'], 'critic_prompt': defaults['critic_prompt'], 'source_chunk_count': len(chunks),
                                 'generation_options': generation_options,
                                 'generation_models': {'embedding':model_evidence(embedding[0],generation_options['embedding_model'],'embedding') if embedding else None,
                                     'generator': model_evidence(config, selection, 'llm'),
                                     'critic': model_evidence(critic_config or config, critic_selection or selection, 'llm') if opts.generation_backend == 'deepeval' else None},
                                 'generation_engine': generation_engine(opts.generation_backend),
                                 'sdk': opts.generation_backend,
                                 'sdk_version': RAGAS_BACKEND_VERSION if opts.generation_backend == 'ragas' else '0.4.3' if opts.generation_backend == 'ragas_native' else '4.3.2'})
        try:
            target.save_sources(dataset['id'], chunks)
        except Exception:
            target.mutate(dataset['id'], lambda c, d: d.update(status='failed', error='资料保存失败，请重新构建。'), generating=True)
            raise
        background_tasks.add_task(build_dataset, target, dataset['id'], chunks, dify, config, key, dataset['prompt'],
                                  generation_options, (critic_config, critic_key) if critic_selection else None, embedding, build_token=dataset['build_token'])
        return dataset

    @router.get('/datasets/{identifier}')
    def get_dataset(identifier: str):
        return repo().get(identifier)

    @router.put('/datasets/{identifier}')
    def update_dataset(identifier: str, payload: DatasetInfo):
        def save(connection, dataset):
            values = payload.model_dump(exclude={'evaluation_purpose'} if 'evaluation_purpose' not in payload.model_fields_set else set())
            if values.get('evaluation_purpose') == 'retrieval' and (dataset.get('source_type') != 'dify' or not knowledge_ids(dataset)):
                raise HTTPException(400, '检索评测数据集必须绑定 Dify 知识库')
            dataset.update(**values)
        return repo().mutate(identifier, save)

    @router.put('/datasets/{identifier}/generation-config')
    def configure_generation(identifier: str, payload: DatasetGenerationConfig):
        target = repo()
        current = target.get(identifier)
        if payload.generation_backend == 'ragas':
            raise HTTPException(400, 'RAGAS 兼容生成已移除，请选择 DeepEval 或 RAGAS 官方生成 0.4.3')
        if current.get('evaluation_purpose') == 'retrieval' and (payload.method != 'contexts' or not knowledge_ids(current)):
            raise HTTPException(400, '检索评测数据集只能使用已同步的 Dify 知识库片段')
        values = generation_values(payload, current.get('evaluation_purpose', 'both'))
        # Concurrency is workspace-owned. Accept old clients but never save an override.
        values.pop('max_concurrent', None)
        if payload.generation_backend != 'deepeval':
            values['critic_llm'] = None
        for field in ['llm', 'critic_llm']:
            if values[field]:
                values[field], _, _ = resolve_llm(values[field])
        if values['embedding_model']:
            values['embedding_model'], _, _ = resolve_embedding(values['embedding_model'])
        # Saving settings never calls a model or changes existing records.
        return target.mutate(identifier, lambda c, d: d.update(generation_config=values))

    def write_sources(connection, identifier, chunks):
        connection.execute('INSERT INTO dataset_sources VALUES (%s,%s) ON CONFLICT (dataset_id) DO UPDATE SET payload=EXCLUDED.payload',
                           (identifier, json.dumps(chunks, ensure_ascii=False)))

    def checked_sources(chunks):
        if sum(len(item.get('content', '')) for item in chunks) > MAX_SOURCE_CHARS:
            raise HTTPException(400, '资料总文本不能超过 200 万字符')
        return chunks

    def read_sources(connection, identifier):
        row = connection.execute('SELECT payload FROM dataset_sources WHERE dataset_id=%s', (identifier,)).fetchone()
        return json.loads(row['payload']) if row else []

    @router.get('/datasets/{identifier}/sources')
    def source_summary(identifier: str):
        target = repo()
        dataset = target.get(identifier)
        configs = {item['id']: item for item in concrete_store().list_service_configs() if item.get('provider') == 'dify'}
        return {'items': [source for source in summarize_sources(target.sources(identifier), dataset, configs) if source['chunk_count']],
                'revision': dataset.get('source_revision', 0), 'dify_config_ids': knowledge_ids(dataset),
                'dify_config_id': dataset.get('dify_config_id'), 'synced_at': dataset.get('dify_synced_at')}

    @router.post('/datasets/{identifier}/documents')
    async def append_documents(identifier: str, files: list[UploadFile] = File(...)):
        target = repo()
        if target.get(identifier).get('evaluation_purpose') == 'retrieval':
            raise HTTPException(400, '检索评测数据集不能导入文档，请同步 Dify 知识库')
        if not 1 <= len(files) <= 20:
            raise HTTPException(400, '请上传 1–20 个文档')
        chunks, total = [], 0
        for file in files:
            data = await read_upload(file)
            total += len(data)
            if total > MAX_UPLOAD:
                raise HTTPException(400, '上传文件总大小不能超过 10 MB')
            name = file.filename or ''
            try:
                parts = await run_in_threadpool(document_chunks, name, data)
            except Exception:
                raise HTTPException(400, '无法解析文档，请使用 TXT、Markdown、文本 PDF、DOCX 或 CSV。') from None
            digest = hashlib.sha256(name.encode() + b'\0' + data).hexdigest()[:20]
            chunks.extend([{**part, 'id': f'document-{digest}-{part["id"]}'} for part in parts])
        def save(connection, dataset):
            old = read_sources(connection, identifier)
            ids = {item['id'] for item in old}
            combined = checked_sources(old + [item for item in chunks if item['id'] not in ids])
            # Deduplicate repeated files within the same upload as well.
            combined = list({item['id']: item for item in combined}.values())
            write_sources(connection, identifier, combined)
            dataset.update(source_chunk_count=sum('content' in item for item in combined),
                           source_revision=dataset.get('source_revision', 0) + 1)
        return await run_in_threadpool(target.mutate, identifier, save)

    @router.post('/datasets/{identifier}/sync-dify')
    async def sync_dify(identifier: str, config_id: str | None = Form(None, min_length=1, max_length=64),
                        config_ids: list[str] = Form(default=[])):
        target = repo()
        before = target.get(identifier)
        if before['status'] == 'generating':
            raise HTTPException(409, '数据集正在构建，请等待完成后再修改')
        ids = list(dict.fromkeys([*config_ids, *([config_id] if config_id else [])]))
        if not ids or len(config_ids) > 100 or len(ids) > 100 or any(not item.strip() or len(item) > 64 for item in ids):
            raise HTTPException(400, '请选择 1–100 个有效知识库')
        accesses = [dify_access(item) for item in ids]
        chunks = []
        try:
            for config, key in accesses:
                chunks.extend(tag_knowledge(await run_in_threadpool(fetch_dify_chunks, config, key), config))
                checked_sources(chunks)
        except Exception:
            raise HTTPException(400, '知识库导入失败，原有资料已保留。请检查配置、连接和资料大小后重试。') from None
        def save(connection, dataset):
            if dataset.get('source_revision', 0) != before.get('source_revision', 0):
                raise HTTPException(409, '资料已被其他操作更新，请重新导入')
            # Refresh selected snapshots only; other sources and existing samples remain intact.
            old = [item for item in read_sources(connection, identifier)
                   if item.get('metadata', {}).get('dify_config_id') not in ids]
            combined = checked_sources(old + chunks)
            if len(combined) > 10000:
                raise HTTPException(400, '来源资料合计不能超过 10000 个片段')
            write_sources(connection, identifier, combined)
            all_ids = list(dict.fromkeys([*knowledge_ids(dataset), *ids]))
            dataset.update(dify_config_id=all_ids[0], dify_config_ids=all_ids, dify_synced_at=utc_now(),
                           source_chunk_count=sum('content' in item for item in combined),
                           source_revision=dataset.get('source_revision', 0) + 1)
        return await run_in_threadpool(target.mutate, identifier, save)

    @router.post('/datasets/{identifier}/build', status_code=202)
    def build_existing(identifier: str, background_tasks: BackgroundTasks,
                       mode: str = Query('append', pattern='^(append|replace_generated)$')):
        target = repo()
        current = target.get(identifier)
        if current['status'] == 'generating':
            raise HTTPException(409, '数据集正在构建，请等待完成后再修改')
        defaults = settings()
        values = current.get('generation_config') or current.get('generation_options') or {}
        try:
            config_values = DatasetGenerationConfig.model_validate(values)
        except ValueError:
            raise HTTPException(400, '生成配置无效，请先配置模型与生成方式') from None
        if current.get('evaluation_purpose') == 'retrieval' and (config_values.method != 'contexts' or current.get('source_type') != 'dify' or not knowledge_ids(current)):
            raise HTTPException(400, '检索评测数据集只能使用 Dify 知识库片段构建')
        options = generation_values(config_values, current.get('evaluation_purpose', 'both'), exclude={'prompt'})
        if not current.get('generation_config') and options['method'] == 'goldens':
            options['seed_dataset_id'] = identifier
        selection, config, key = resolve_llm(options['llm'] or defaults['default_llm'])
        critic_selection = (options['critic_llm'] or defaults['default_critic_llm']) if options['generation_backend'] == 'deepeval' else None
        critic_selection, critic_config, critic_key = resolve_llm(critic_selection) if critic_selection else (None, None, None)
        options.update(llm=selection, critic_llm=critic_selection, max_concurrent=defaults['max_concurrent'])
        embedding = embedding_access(options)
        prompt = config_values.prompt or defaults['prompt']
        # Persist the exact source snapshot under the parent lock before starting a worker.
        def start(connection, dataset):
            if dataset['updated_at'] != current['updated_at']:
                raise HTTPException(409, '数据集已更新，请刷新后重新构建')
            stored = read_sources(connection, identifier)
            if options['method'] == 'contexts':
                chunks = [item for item in stored if 'content' in item]
                if not chunks:
                    raise HTTPException(400, '请先导入文档或同步 Dify 知识库，再开始构建')
            elif options['method'] == 'goldens':
                seed_id = options['seed_dataset_id']
                seed = target.get(seed_id)
                if seed['status'] == 'generating':
                    raise HTTPException(409, '种子数据集正在构建')
                chunks = [{'id': item['id'], 'golden': golden_dict(item),
                           'metadata': {'seed_dataset_id': seed_id, 'seed_item_id': item['id']}}
                          for item in target.items(seed_id, size=20000)['items'] if item['status'] == 'active']
                if not chunks or len({bool(item['golden'].get('context')) for item in chunks}) > 1:
                    raise HTTPException(400, '种子需包含启用数据，且参考上下文需全部存在或全部为空')
                write_sources(connection, identifier, [item for item in stored if 'content' in item] + chunks)
            else:
                chunks = []
            rows = connection.execute('SELECT payload FROM dataset_items WHERE dataset_id=%s', (identifier,)).fetchall()
            baseline = sum(not target.is_generated(json.loads(row['payload']), dataset) for row in rows) if mode == 'replace_generated' else len(rows)
            if baseline + options['size'] > 20000:
                raise HTTPException(400, '生成后将超过 20000 条数据，请调整数量')
            dataset.update(build_state())
            dataset.update(generation_models={'embedding':model_evidence(embedding[0],options['embedding_model'],'embedding') if embedding else None,
                           'generator': model_evidence(config, selection, 'llm'),
                           'critic': model_evidence(critic_config or config, critic_selection or selection, 'llm') if options['generation_backend'] == 'deepeval' else None},
                           status='generating', error=None, llm=selection, generation_options=options, prompt=prompt, critic_prompt=defaults['critic_prompt'],
                           target_size=baseline + options['size'], build_mode=mode, build_baseline=baseline,
                           build_target=options['size'], build_progress=0, generation_engine=generation_engine(options['generation_backend']), sdk=options['generation_backend'],
                           sdk_version=RAGAS_BACKEND_VERSION if options['generation_backend'] == 'ragas' else '0.4.3' if options['generation_backend'] == 'ragas_native' else '4.3.2')
            return chunks
        selected_chunks = []
        def locked_start(connection, dataset):
            selected_chunks.extend(start(connection, dataset))
        dataset = target.mutate(identifier, locked_start)
        background_tasks.add_task(build_dataset, target, identifier, selected_chunks, None, config, key, prompt,
                                  options, (critic_config, critic_key) if critic_selection else None, embedding, build_token=dataset['build_token'])
        return dataset

    @router.post('/datasets/{identifier}/regenerate-answers', status_code=202)
    def regenerate_dataset_answers(identifier: str, payload: BulkIds, background_tasks: BackgroundTasks):
        target = repo()
        current = target.get(identifier)
        if current['status'] == 'generating':
            raise HTTPException(409, '数据集正在构建，请等待完成后再修改')
        try:
            rows = target.selected_items(identifier, payload.ids)
        except HTTPException:
            raise
        eligible = [item for item in rows if item.get('status', 'active') == 'active'
                    and isinstance(item.get('input'), str) and item.get('input').strip()
                    and isinstance(item.get('context'), list) and any(str(value).strip() for value in item['context'])]
        if not eligible:
            raise HTTPException(400, '所选数据项没有可用的参考上下文，无法重新生成答案')
        defaults = settings()
        values = current.get('generation_config') or current.get('generation_options') or {}
        try:
            config_values = DatasetGenerationConfig.model_validate(values)
        except ValueError:
            raise HTTPException(400, '生成配置无效，请先配置模型与生成方式') from None
        selection, config, key = resolve_llm(config_values.llm or current.get('llm') or defaults['default_llm'])
        options = generation_values(config_values, current.get('evaluation_purpose', 'both'), exclude={'prompt'})
        options.update(llm=selection, max_concurrent=defaults['max_concurrent'])
        prompt = config_values.prompt or current.get('prompt') or defaults['prompt']
        ids = [item['id'] for item in eligible]
        def start(connection, dataset):
            dataset.update(build_state(), status='generating', error=None,
                           llm=selection, generation_options=options,
                           target_size=len(ids), build_mode='regenerate_answers', build_baseline=0,
                           build_target=len(ids), build_progress=0,
                           build_answer_item_ids=ids, build_answer_pending_ids=ids,
                           build_answer_total=len(ids), build_stage='generating',
                           generation_models={'generator': model_evidence(config, selection, 'llm')})
        dataset = target.mutate(identifier, start)
        background_tasks.add_task(regenerate_answers, target, identifier, ids, config, key, prompt,
                                  options, dataset['build_token'])
        return dataset

    @router.delete('/datasets/{identifier}', status_code=204)
    def delete_dataset(identifier: str):
        repo().delete(identifier)

    @router.post('/datasets/{identifier}/cancel')
    def cancel_dataset(identifier: str):
        def cancel(c, d):
            if d['status'] == 'cancelled':
                return
            if d['status'] != 'generating':
                raise HTTPException(409, '当前没有正在生成的任务')
            d.update(status='cancelled', build_token=None, build_stage='cancelled',
                     build_active_requests=0, build_cancelled_at=utc_now(), error=None)
        return repo().mutate(identifier, cancel, generating=True)

    @router.post('/datasets/{identifier}/retry', status_code=202)
    def retry_dataset(identifier: str, background_tasks: BackgroundTasks):
        target = repo()
        dataset = target.get(identifier)
        if dataset['status'] not in {'failed', 'cancelled'} or not dataset.get('target_size'):
            raise HTTPException(409, '只能重试未完成的数据集构建')
        if dataset.get('build_mode') == 'regenerate_answers':
            pending = dataset.get('build_answer_pending_ids') or dataset.get('build_answer_item_ids') or []
            if not pending:
                raise HTTPException(409, '没有待重新生成答案的数据项')
            values = dataset.get('generation_options') or dataset.get('generation_config') or {}
            try:
                config_values = DatasetGenerationConfig.model_validate(values)
            except ValueError:
                raise HTTPException(400, '生成配置无效，请先配置模型与生成方式') from None
            defaults = settings()
            selection, config, key = resolve_llm(config_values.llm or dataset.get('llm') or defaults['default_llm'])
            options = generation_values(config_values, dataset.get('evaluation_purpose', 'both'), exclude={'prompt'})
            options.update(llm=selection, max_concurrent=defaults['max_concurrent'])
            prompt = config_values.prompt or dataset.get('prompt') or defaults['prompt']
            total = dataset.get('build_answer_total') or dataset.get('build_target') or len(pending)
            def start_answers(c, d):
                if d['status'] not in {'failed', 'cancelled'}:
                    raise HTTPException(409, '数据集正在构建')
                d.update(build_state(), status='generating', error=None, llm=selection,
                         generation_options=options, target_size=total, build_target=total,
                         build_progress=d.get('build_progress', 0), build_mode='regenerate_answers',
                         build_answer_pending_ids=pending,
                         generation_models={'generator': model_evidence(config, selection, 'llm')})
            dataset = target.mutate(identifier, start_answers)
            background_tasks.add_task(regenerate_answers, target, identifier, pending, config, key,
                                      prompt, options, dataset['build_token'])
            return dataset
        selection, config, key = resolve_llm(dataset.get('llm') or settings()['default_llm'])
        opts = {**dataset.get('generation_options', {}), 'max_concurrent': settings()['max_concurrent']}
        if dataset.get('evaluation_purpose') == 'retrieval' and (opts.get('method') != 'contexts' or dataset.get('source_type') != 'dify' or not knowledge_ids(dataset)):
            raise HTTPException(400, '检索评测数据集只能使用 Dify 知识库片段构建')
        dify = [dify_access(item) for item in knowledge_ids(dataset)] if knowledge_ids(dataset) and not dataset.get('build_mode') else None
        chunks = target.sources(identifier)
        chunks = [item for item in chunks if ('golden' in item if opts.get('method') == 'goldens' else 'content' in item)] if opts.get('method') != 'scratch' else []
        embedding = embedding_access(opts, inherit_default=False)
        critic = resolve_llm(opts['critic_llm'])[1:] if opts.get('generation_backend', 'deepeval') == 'deepeval' and opts.get('critic_llm') else None
        if opts.get('method') != 'scratch' and not dify and not chunks:
            raise HTTPException(400, '源文档不存在，请重新上传构建')
        def start(c, d):
            if d['status'] not in {'failed', 'cancelled'}:
                raise HTTPException(409, '数据集正在构建')
            d.update(build_state())
            d.update(status='generating', error=None, llm=selection, generation_options=opts)
        dataset = target.mutate(identifier, start)
        prompt = dataset.get('prompt') or settings()['prompt']
        if prompt == LEGACY_DEFAULT_PROMPT:
            prompt = DEFAULT_PROMPT
        background_tasks.add_task(build_dataset, target, identifier, chunks, dify, config, key, prompt, opts, critic, embedding, build_token=dataset['build_token'])
        return dataset

    @router.get('/datasets/{identifier}/items')
    def list_items(identifier: str, page: int = Query(1, ge=1, le=1000000000), page_size: int = Query(20, ge=1, le=100), q: str = Query('', max_length=200)):
        return repo().items(identifier, page, page_size, q)

    @router.get('/datasets/{identifier}/items/{item_id}/generation')
    def item_generation(identifier: str, item_id: str):
        return DatasetTraceStore(repo()).get(identifier, item_id)

    @router.get('/datasets/{identifier}/items/{item_id}/generation/requests')
    def generation_requests(identifier: str, item_id: str, page: int = Query(1, ge=1, le=1000000000), page_size: int = Query(10, ge=1, le=100)):
        return DatasetTraceStore(repo()).requests(identifier, item_id, page, page_size)

    @router.get('/datasets/{identifier}/items/{item_id}/generation/requests/{request_id}')
    def generation_request(identifier: str, item_id: str, request_id: str):
        return DatasetTraceStore(repo()).request(identifier, item_id, request_id)

    @router.get('/datasets/{identifier}/items/selection')
    def select_items(identifier: str, q: str = Query('', max_length=200)):
        return repo().selection(identifier, q)

    @router.post('/datasets/{identifier}/items/bulk')
    def bulk_items(identifier: str, payload: ItemBulk):
        return repo().bulk_items(identifier, payload.ids, payload.action)

    @router.post('/datasets/{identifier}/items', status_code=201)
    def add_item(identifier: str, payload: DatasetItem):
        if repo().get(identifier).get('evaluation_purpose') == 'retrieval':
            raise HTTPException(400, '检索评测数据集不能手动添加数据，请从 Dify 知识库生成')
        return repo().add(identifier, [payload.model_dump()])

    @router.put('/datasets/{identifier}/items/{item_id}')
    def update_item(identifier: str, item_id: str, payload: DatasetItem):
        if repo().get(identifier).get('evaluation_purpose') == 'retrieval':
            raise HTTPException(400, '检索评测数据集不能手动编辑数据')
        return repo().update_item(identifier, item_id, payload.model_dump())

    @router.delete('/datasets/{identifier}/items/{item_id}', status_code=204)
    def delete_item(identifier: str, item_id: str):
        repo().update_item(identifier, item_id)

    @router.post('/datasets/{identifier}/import')
    async def append_import(identifier: str, file: UploadFile = File(...)):
        if repo().get(identifier).get('evaluation_purpose') == 'retrieval':
            raise HTTPException(400, '检索评测数据集不能导入数据，请从 Dify 知识库生成')
        data = await read_upload(file)
        try:
            items = await run_in_threadpool(parse_import, file.filename or '', data)
        except (ValueError, RecursionError):
            raise HTTPException(400, '导入格式无效，请检查文件内容、列名和数据类型') from None
        return await run_in_threadpool(repo().add, identifier, items, origin='import')

    @router.get('/datasets/{identifier}/export')
    def export_dataset(identifier: str, format: str = Query('json', pattern='^(json|jsonl|csv)$')):
        return export_response(identifier, repo().items(identifier, size=20000)['items'], format)

    @router.post('/datasets/{identifier}/export')
    def export_selected_items(identifier: str, payload: BulkIds, format: str = Query('json', pattern='^(json|jsonl|csv)$')):
        return export_response(identifier, repo().selected_items(identifier, payload.ids), format)

    def export_response(identifier, records, format):
        items = [golden_dict(item) for item in records]
        if format == 'csv':
            output = io.StringIO(newline='')
            fields = list(items[0]) if items else list(golden_dict({'input': 'example'}))
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for item in items:
                cells = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in item.items()}
                cells['context'] = join_context(item.get('context'))
                cells['retrieval_context'] = join_retrieval_context(item.get('retrieval_context'))
                writer.writerow(cells)
            content, media_type = '\ufeff' + output.getvalue(), 'text/csv'
        elif format == 'jsonl':
            content, media_type = ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in items), 'application/x-ndjson'
        else:
            content, media_type = json.dumps(items, ensure_ascii=False, indent=2), 'application/json'
        return Response(content, media_type=media_type, headers={'Content-Disposition': f'attachment; filename="dataset-{identifier}.{format}"'})

    app.include_router(router)
