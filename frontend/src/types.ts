export type AuthUser = { id: string; provider: string; username: string; name: string; nickname?: string; avatar_url?: string | null };
export type ModelCredential = { id?: string; name: string; server_url: string; model_uid?: string | null; api_key?: string | null; copy_key_from?: string | null; has_api_key?: boolean; api_key_masked?: string | null };
export type ModelConfig = { enabled?: boolean; name: string | null; alias?: string | null; benchmark: string; base_url: string | null; credentials?: ModelCredential[] };
export type ProviderIcon = "cube" | "spark" | "cloud" | "bolt" | "waves" | "database";

export type Provider = {
  dataset_id?: string | null;
  id: string;
  provider?: string;
  label: string;
  model: string | null;
  models: ModelConfig[];
  icon: ProviderIcon;
  endpoint_configured: boolean;
  credential_configured: boolean;
  credential_required: boolean;
};

export type Benchmark = {
  id: string;
  label: string;
  providers: Provider[];
  provider_kinds: Array<{ id: string; label: string }>;
};
export type Catalog = { benchmarks: Benchmark[] };
export type LlmSelection = { id: string; model: string; credential_id?: string | null };
export type SystemSettings = { default_llm: LlmSelection | null; updated_at?: string };
export type AiReport = { run_id: string; status: "none" | "generating" | "ready" | "failed"; content: string | null; error?: string | null; generated_at?: string; model?: { provider: string; model: string }; requested_model?: { provider: string; model: string } };
export type PlaygroundResult = { ok: boolean; status_code: number; duration_ms: number; data: unknown; truncated: boolean };
export type LlmParameter = { type: "integer" | "number" | "enum"; label: string; description: string; min?: number; max?: number; step?: number; default?: string | number; values: string[]; enabled_when: Record<string, string> };
export type LlmParameters = { model: string; provider: string; version: string; source?: string; model_specific: boolean; can_refresh: boolean; metadata: { context_window?: number; context_length?: number; max_output_tokens?: number; fetched_at?: string }; parameters: Record<string, LlmParameter> };

export type ServiceConfig = {
  enabled?: boolean;
  app_id?: string | null;
  dataset_id?: string | null;
  id: string;
  name: string;
  benchmark: string;
  provider: string;
  base_url: string | null;
  model: string | null;
  models: ModelConfig[];
  icon: ProviderIcon;
  has_api_key: boolean;
  api_key_masked?: string | null;
  endpoint_configured: boolean;
};

export type MetricSummary = {
  count: number;
  avg: number;
  p50: number;
  p95: number;
  p99: number;
  min: number;
  max: number;
};

export type Scenario = {
  id: string;
  provider: string;
  provider_name: string;
  model: string | null;
  concurrency: number;
  status: string;
  completed_requests: number;
  total_requests: number;
  started_at?: string;
  result: null | {
    success_rate: number;
    qps_success: number;
    success_count: number;
    failure_count: number;
    samples?: Array<{ completion_order?: number; ok: boolean; latency_ms: number }>;
    metrics: Record<string, MetricSummary>;
  };
  error: string | null;
};

export type Run = {
  id: string;
  plan_id: string;
  name: string;
  benchmark: string;
  status: string;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_scenarios: number;
  total_scenarios: number;
  concurrency_levels: number[];
  requests_per_scenario: number;
  query?: string | null;
  documents?: string[];
  scenarios: Scenario[];
  error: string | null;
};

export type TestPlan = {
  max_tokens?: number;
  dataset_id?: string | null;
  id: string;
  name: string;
  benchmark: string;
  providers: Array<{ id: string; model?: string | null; credential_id?: string | null }>;
  concurrency_levels: number[];
  requests_per_scenario: number;
  timeout_seconds: number;
  query?: string | null;
  documents?: string[];
  status: "active" | "paused" | "stopped" | "completed" | "error";
  start_at: string | null;
  next_run_at: string | null;
  repeat_mode: "once" | "count" | "forever";
  repeat_count: number | null;
  repeat_interval_seconds: number | null;
  run_count: number;
  schedule_run_count: number;
  last_run_id: string | null;
  last_run_at: string | null;
  schedule_error: string | null;
  created_at: string;
  updated_at: string;
};

export type DatasetEvaluationSummary = {
  id: string; name: string; status: string; created_at: string; case_count: number;
  metrics: Record<string, { mean: number | null; scored: number }>;
};
export type Dataset = {
  id: string; name: string; description: string; status: 'ready' | 'generating' | 'failed' | 'cancelled';
  evaluation_purpose?: 'rag' | 'retrieval' | 'both';
  source_type: 'manual' | 'import' | 'documents' | 'dify' | 'goldens' | 'scratch'; item_count: number; target_size?: number;
  llm?: LlmSelection; sdk?: string; sdk_version?: string; generation_usage?: { requests: number; output_tokens: number; cost: number | null }; source_chunk_count?: number; error?: string | null; created_at: string; updated_at: string;
  data_sources?: DatasetSource[]; dify_config_id?: string | null; dify_config_ids?: string[]; generation_config?: DatasetGenerationOptions; generation_options?: DatasetGenerationOptions;
  build_mode?: 'append' | 'replace_generated' | 'regenerate_answers'; build_progress?: number; build_target?: number;
  build_retry_count?: number; build_retry_limit?: number; build_last_error?: string | null;
  build_stage?: string; build_requests?: number; build_active_requests?: number; build_output_tokens?: number;
  build_started_at?: string; build_request_started_at?: string | null;
  latest_evaluation?: DatasetEvaluationSummary | null;
};
export type DatasetGenerationOptions = {
  generation_backend?: 'deepeval' | 'ragas' | 'ragas_native'; embedding_model?: LlmSelection | null; ragas_query_distribution?:Record<string,number>; ragas_personas?:Array<{name:string;role_description:string}>; size?: number; method?: 'contexts' | 'goldens' | 'scratch'; llm?: LlmSelection | null; critic_llm?: LlmSelection | null;
  seed_dataset_id?: string | null; dify_config_id?: string | null; dify_config_ids?: string[]; prompt?: string | null;
  include_expected_output?: boolean; quality_threshold?: number; max_quality_retries?: number; num_evolutions?: number;
  max_concurrent?: number; context_size?: number; evolutions?: Record<string, number>;
  task?: string; scenario?: string; input_format?: string; expected_output_format?: string;
};
export type DatasetSource = { kind: 'documents' | 'dify' | 'import' | 'manual' | 'goldens' | 'scratch'; name: string; chunk_count: number; config_id?: string; dataset_id?: string };
export type DatasetSources = { items: DatasetSource[]; revision: number; dify_config_id?: string | null; dify_config_ids?: string[]; synced_at?: string | null };
export type Golden = {
  input: string; expected_output?: string | null; context?: string[] | null;
  actual_output?: string | null; retrieval_context?: unknown[] | null;
  additional_metadata?: Record<string, unknown> | null; comments?: string | null;
  expected_tools?: unknown[] | null; tools_called?: unknown[] | null;
  expected_labels?: Record<string, string> | null; expectations?: Record<string, unknown> | null;
  source_file?: string | null; name?: string | null; custom_column_key_values?: Record<string, string> | null;
  [key: string]: unknown;
};
export type DatasetItem = Golden & {
  generation_trace_id?: string; generation_record_id?: string;
  id: string; dataset_id: string; origin?: 'manual' | 'import' | 'generated'; status: 'active' | 'archived'; created_at: string; updated_at: string;
};
export type DatasetSettings = { default_reranker?: LlmSelection | null; max_concurrent: number; default_embedding_model: LlmSelection | null; default_judge_llm: LlmSelection | null; scoring_engine: 'deepeval' | 'ragas'; judge_prompt: string; default_judge_prompt: string; default_llm: LlmSelection | null; default_critic_llm: LlmSelection | null; prompt: string; default_prompt: string; critic_prompt: string; default_critic_prompt: string };

export type GenerationDetails = {
  availability: 'recorded' | 'unavailable'; edited: boolean;
  sample: {input: string; expected_output?: string | null; context?: string[] | null};
  trace: null | {id: string; created_at: string; backend: string; method: string; prompt: string; critic_prompt?: string;
    options: Record<string, unknown>; models: Record<string, {model?: string} | null>;
    sources: Array<{content?: string; golden?: Golden; metadata?: Record<string, unknown>}>};
};
export type GenerationRequestSummary = {id: string; sequence: number | string; created_at: string; role: string; model: string};
export type GenerationRequest = GenerationRequestSummary & {request: {model: string; messages: Array<{role: string; content: string}>; [key: string]: unknown}};
