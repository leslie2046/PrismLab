"""Versioned quality-evaluation contracts. Execution adapters arrive in M1/M2."""
from copy import deepcopy
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.ragas_generation import RAGAS_BACKEND_VERSION
from backend.schemas import ProviderSelection

CONTRACT_VERSION = '2.0'
RUN_STATES = ['prepared', 'running', 'completed', 'partial_failed', 'failed', 'cancelled']
CASE_STATES = ['pending', 'completed', 'target_error', 'cancelled']
METRIC_STATES = ['pending', 'completed', 'judge_error', 'missing_data', 'cancelled']
DEFAULT_JUDGE_PROMPT = 'Evaluate the supplied answer or retrieval results against the selected metric and evidence. Explain the score; do not invent missing references or retrieved context.'


def generation_engine(name):
    if name == 'ragas_native':
        return {'id':'ragas_native','implementation':'ragas.testset.TestsetGenerator','adapter_version':'1', 'sdk_version':'0.4.3','official_sdk':True}
    if name == 'ragas':
        return {'id': 'ragas', 'implementation': 'prismlab.ragas-compatible',
                'adapter_version': RAGAS_BACKEND_VERSION, 'sdk_version': None, 'official_sdk': False}
    if name == 'deepeval':
        return {'id': 'deepeval', 'implementation': 'deepeval.synthesizer',
                'adapter_version': '1', 'sdk_version': '4.3.2', 'official_sdk': True}
    return {'id': 'none', 'implementation': 'manual-or-import', 'adapter_version': '1', 'sdk_version': None, 'official_sdk': False}


def scoring_engine(name):
    return {'id': name, 'implementation': f'prismlab.{name}.evaluation',
            'adapter_version': '2.0', 'sdk_version': {'deepeval': '4.3.2', 'ragas': '0.4.3'}.get(name),
            'executor_available': True}


METRICS = {
    'deepeval.answer_relevancy': {'engine': 'deepeval', 'implementation': 'deepeval.metrics.AnswerRelevancyMetric',
        'required_fields': ['input', 'actual_output']},
    'deepeval.faithfulness': {'engine': 'deepeval', 'implementation': 'deepeval.metrics.FaithfulnessMetric',
        'required_fields': ['actual_output', 'retrieval_context']},
    'deepeval.reference_correctness': {'engine': 'deepeval', 'implementation': 'deepeval.metrics.GEval:reference-correctness-v1',
        'required_fields': ['input', 'expected_output', 'actual_output']},
    'deepeval.context_precision': {'engine': 'deepeval', 'implementation': 'deepeval.metrics.ContextualPrecisionMetric',
        'required_fields': ['input', 'expected_output', 'retrieval_context']},
    'deepeval.context_recall': {'engine': 'deepeval', 'implementation': 'deepeval.metrics.ContextualRecallMetric',
        'required_fields': ['input', 'expected_output', 'retrieval_context']},
    'ragas.faithfulness': {'engine': 'ragas', 'implementation': 'ragas.metrics.Faithfulness',
        'required_fields': ['input', 'actual_output', 'retrieval_context']},
    'ragas.context_recall': {'engine': 'ragas', 'implementation': 'ragas.metrics.LLMContextRecall',
        'required_fields': ['input', 'expected_output', 'retrieval_context']},
    'ragas.context_precision': {'engine': 'ragas', 'implementation': 'ragas.metrics.LLMContextPrecisionWithReference',
        'required_fields': ['input', 'expected_output', 'retrieval_context']},
    'ragas.context_precision_without_reference': {'engine': 'ragas', 'implementation': 'ragas.metrics.LLMContextPrecisionWithoutReference',
        'required_fields': ['input', 'actual_output', 'retrieval_context']},
    'ragas.answer_accuracy': {'engine': 'ragas', 'implementation': 'ragas.metrics.FactualCorrectness',
        'required_fields': ['expected_output', 'actual_output']},
    'rules.recall_at_k': {'engine': 'rules', 'implementation': 'prismlab.ranking.recall-v1',
        'required_fields': ['relevant_chunk_ids', 'retrieved_chunk_ids'], 'empty_retrieval_valid': True},
    'rules.mrr': {'engine': 'rules', 'implementation': 'prismlab.ranking.mrr-v1',
        'required_fields': ['relevant_chunk_ids', 'retrieved_chunk_ids'], 'empty_retrieval_valid': True},
    'rules.ndcg': {'engine': 'rules', 'implementation': 'prismlab.ranking.ndcg-v1',
        'required_fields': ['graded_relevance', 'retrieved_chunk_ids'], 'empty_retrieval_valid': True},
    'deepeval.custom': {'engine': 'deepeval', 'implementation': 'deepeval.metrics.GEval:rubric-v1',
        'required_fields': ['input', 'actual_output']},
    'rules.required_text': {'engine': 'rules', 'implementation': 'prismlab.required-text-v1',
        'required_fields': ['actual_output']},
    'rules.json_schema': {'engine': 'rules', 'implementation': 'jsonschema.Draft202012Validator',
        'required_fields': ['actual_output']},
}

RETRIEVAL_METRICS = frozenset({
    'deepeval.context_precision', 'deepeval.context_recall',
    'ragas.context_precision', 'ragas.context_recall',
    'rules.recall_at_k', 'rules.mrr', 'rules.ndcg',
})


def evaluation_type(evidence):
    """Classify legacy evidence without rewriting immutable historical records."""
    if evidence.get('evaluation_type') in {'rag', 'retrieval'}:
        return evidence['evaluation_type']
    if evidence.get('target', {}).get('benchmark') == 'dify-retrieve':
        return 'retrieval'
    return 'rag'


def metric_catalog():
    return [{'id': key, 'evaluation_types': ['rag', 'retrieval'] if key in RETRIEVAL_METRICS else ['rag'], 'sample_kinds':['single_turn'], **deepcopy(value), 'contract_version': CONTRACT_VERSION,
             'implementation_version': {'deepeval': '4.3.2/adapter-2', 'ragas': '0.4.3/adapter-2', 'rules': '1'}[value['engine']],
             'executor_available': True, 'score_range': [0.0, 1.0],
             'threshold': {'operator': 'gte', 'default': 0.5},
             'model_dependencies': [] if value['engine'] == 'rules' else ['result_judge'],
             'usage': {'requests': 'measured', 'tokens': 'provider_dependent', 'cost': 'unknown_without_pricing'}}
            for key, value in METRICS.items()]


def present(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(present(item) for item in value)
    return value is not None


def dependency_check(metric, sample, observation=None):
    """Imported Golden observations are never observations of this run."""
    observation = observation or {}
    missing, awaiting = [], []
    annotations = sample.get('annotations', {})
    for field in metric['required_fields']:
        if field in {'actual_output', 'retrieval_context', 'retrieved_chunk_ids', 'turns'}:
            if field == 'retrieved_chunk_ids' and metric.get('empty_retrieval_valid') and isinstance(observation.get(field), list):
                continue
            if not present(observation.get(field)):
                (awaiting if observation == {} else missing).append(field)
        elif not present(sample.get(field, annotations.get(field))):
            missing.append(field)
    return {'data_status': 'missing_data' if missing else 'awaiting_target' if awaiting else 'ready',
            'missing_fields': missing, 'awaiting_fields': awaiting,
            'executor_available': metric['executor_available'],
            'reasons': [f'missing:{field}' for field in missing] +
                       [f'awaiting_target:{field}' for field in awaiting] +
                       ([] if metric['executor_available'] else ['executor_not_implemented'])}


def clean_url(value):
    try:
        parts = urlsplit(value)
        if parts.scheme not in {'http', 'https'} or not parts.hostname:
            return None
        host = parts.hostname
        if ':' in host:
            host = f'[{host}]'
        if parts.port:
            host += f':{parts.port}'
        return urlunsplit((parts.scheme, host, parts.path, '', ''))
    except ValueError:
        return None


SECRET_KEYS = {'api_key', 'apikey', 'api_key_encrypted', 'authorization', 'password', 'secret',
               'token', 'access_token', 'refresh_token', 'credential', 'credentials', 'headers', 'api_key_env', 'x-api-key', 'api-key'}


def safe_evidence(value):
    """Strip credential structures and sanitize URLs in provenance/metadata."""
    if isinstance(value, dict):
        if value.get('dify_config_id') and value.get('dataset_id'):
            value = {**value, 'external_dify_dataset_id': value['dataset_id']}
        return {key: (item if (isinstance(item, str) or isinstance(item, list) and all(isinstance(part, str) for part in item)) and key in {'input', 'expected_output', 'actual_output', 'content', 'context', 'retrieval_context', 'prompt', 'critic_prompt'}
                      else safe_evidence(item)) for key, item in value.items()
                if key.lower() not in SECRET_KEYS and not key.lower().endswith(('_key', '_secret', '_token', '_encrypted'))}
    if isinstance(value, list):
        return [safe_evidence(item) for item in value]
    if isinstance(value, str) and value.startswith(('https://', 'http://')):
        return clean_url(value)
    return value


def model_evidence(config, selection, benchmark):
    # Deliberate allowlist: resolved configs include encrypted credentials/models.
    return {'selection': {**{key: selection.get(key) for key in ('id', 'model', 'credential_id')},
                          'credential_id': config.get('credential_id') or selection.get('credential_id')},
            'benchmark': benchmark, 'provider': config.get('provider'), 'name': config.get('name'),
            'model': config.get('model'), 'model_revision': config.get('model_revision'), 'config_updated_at': config.get('updated_at'),
            'base_url': clean_url(config.get('base_url') or ''),
            'external_dify_dataset_id': config.get('dataset_id') if benchmark == 'dify-retrieve' else None,
            'external_dify_app_id': config.get('app_id') if benchmark == 'dify-chat' else None}


class StrictContract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class EvaluationTarget(StrictContract):
    benchmark: Literal['llm', 'dify-chat', 'dify-retrieve']
    selection: ProviderSelection
    prompt: str = Field(default='', max_length=20000)
    max_tokens: int = Field(default=1024, ge=2, le=8192)
    timeout_seconds: int = Field(default=60, ge=5, le=180)
    retrieval_top_k: int = Field(default=5, ge=1, le=100)
    selections: list[ProviderSelection] = Field(default_factory=list, max_length=20)
    retrieval_reranker: ProviderSelection | None = None
    retrieval_candidate_top_k: int = Field(default=20, ge=1, le=100)
    retrieval_score_threshold: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode='after')
    def retrieval_options(self):
        if self.benchmark != 'dify-retrieve' and (self.selections or self.retrieval_reranker or self.retrieval_score_threshold is not None):
            raise ValueError('多知识库与重排配置仅用于 Dify 知识库检索评测')
        if self.selections:
            if self.selections[0] != self.selection:
                raise ValueError('主知识库必须与知识库列表第一项一致')
            if len({s.id for s in self.selections}) != len(self.selections):
                raise ValueError('不能重复选择同一知识库')
        if (self.selections or self.retrieval_reranker or self.retrieval_score_threshold is not None) and self.retrieval_candidate_top_k < self.retrieval_top_k:
            raise ValueError('每库召回数量不能小于最终 Top K')
        return self


class MetricSelection(StrictContract):
    id: str
    threshold: float = Field(default=0.5, ge=0, le=1)
    criteria: str = Field(default='', max_length=10000)
    examples: str = Field(default='', max_length=10000)
    required_text: list[str] = Field(default_factory=list, max_length=50)
    json_schema: dict | None = None
    k: int = Field(default=5, ge=1, le=100)

    @model_validator(mode='after')
    def registered(self):
        if self.id not in METRICS:
            raise ValueError('Unknown metric')
        if self.id == 'deepeval.custom' and not self.criteria.strip():
            raise ValueError('Custom judge criteria are required')
        if self.id == 'rules.required_text' and (not self.required_text or any(not t.strip() or len(t)>2000 for t in self.required_text)):
            raise ValueError('Required text must contain nonempty phrases')
        if self.id == 'rules.json_schema':
            import jsonschema
            if self.json_schema is None:
                raise ValueError('JSON Schema is required')
            try:
                jsonschema.Draft202012Validator.check_schema(self.json_schema)
            except jsonschema.SchemaError:
                raise ValueError('Invalid JSON Schema') from None
        return self


class EvaluationBudget(StrictContract):
    max_requests: int = Field(default=500, ge=1, le=100000)
    max_output_tokens: int = Field(default=500000, ge=256, le=100000000)
    judge_max_tokens: int = Field(default=2048, ge=256, le=8192)
    max_retries: int = Field(default=2, ge=0, le=4)


class PrepareEvaluation(StrictContract):
    dataset_id: str | None = Field(default=None, min_length=1, max_length=64)
    dataset_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(default='Quality evaluation', min_length=1, max_length=120)
    evaluation_type: Literal['rag', 'retrieval'] | None = None
    template: Literal['reference_qa', 'reference_free', 'retrieve_only', 'retrieval'] = 'reference_qa'
    sample_kinds: list[Literal['single_turn']] = Field(default_factory=lambda:['single_turn'],min_length=1,max_length=1)
    sample_limit: int | None = Field(default=None, ge=1, le=20000)
    budget: EvaluationBudget = Field(default_factory=EvaluationBudget)
    use_cache: bool = True
    target: EvaluationTarget
    scoring_engine: Literal['deepeval', 'ragas', 'rules'] | None = None
    judge: ProviderSelection | None = None
    judge_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    metrics: list[MetricSelection] = Field(default_factory=list, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')

    @model_validator(mode='after')
    def unique_metrics(self):
        if self.evaluation_type == 'retrieval':
            if self.template != 'retrieval' or self.target.benchmark != 'dify-retrieve':
                raise ValueError('检索评测需要 Dify 知识库，并使用检索评测模板')
            if self.sample_kinds != ['single_turn']:
                raise ValueError('检索评测仅支持单轮问题')
            if any(m.id not in RETRIEVAL_METRICS for m in self.metrics):
                raise ValueError('检索评测只能选择无需实际回答的检索指标')
        elif self.evaluation_type == 'rag':
            if self.target.benchmark == 'dify-retrieve' or self.template in {'retrieval', 'retrieve_only'}:
                raise ValueError('RAG 评测需要返回回答的应用或模型')
        elif self.template == 'retrieval':
            raise ValueError('请指定检索评测类型')
        if not self.dataset_id and not self.dataset_version_id:
            raise ValueError('Choose a dataset or frozen version')
        if not self.metrics and self.template != 'retrieve_only':
            raise ValueError('Choose at least one metric')
        if self.template == 'retrieve_only' and self.target.benchmark != 'dify-retrieve':
            raise ValueError('Retrieval collection requires a knowledge base target')
        if len({m.id for m in self.metrics}) != len(self.metrics):
            raise ValueError('Duplicate metrics')
        if self.judge_prompt is not None and not self.judge_prompt.strip():
            raise ValueError('Judge prompt cannot be blank')
        return self
