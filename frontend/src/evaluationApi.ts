import { authenticatedFetch } from './api';
import type { LlmSelection } from './types';
export type Metric = { id: string; k?:number; k_source?:'dify'|'fusion'; engine: string; evaluation_types?: string[]; required_fields: string[]; executor_available: boolean; sample_kinds:string[]; threshold: {default:number;value?:number;operator?:'gte'}; implementation_version:string };
export type DifyRetrievalModel = {search_method?:string;reranking_enable?:boolean;reranking_mode?:string;reranking_model?:{provider_name?:string;model_name?:string;reranking_provider_name?:string;reranking_model_name?:string;model?:string;name?:string}|string|null;reranking_provider_name?:string;reranking_model_name?:string;top_k?:number;score_threshold_enabled?:boolean;score_threshold?:number};
export type CaseResult = {id:string; sample_id:string; status:string; error:string|null; observation: {actual_output:string|null; retrieval_context:string[]|null; retrieved_chunk_ids?:string[]|null; duration_ms:number|null; metadata:unknown; turns?:unknown; tools_called?:unknown}|null};
export type MetricResult = {id:string;case_id:string;metric_id:string;status:string;score:number|null;passed:boolean|null;reason:string|null;error:string|null;dependency:{missing_fields:string[];awaiting_fields:string[]};usage?:{cache_hit?:boolean}};
export type Evidence = {evaluation_type?:'rag'|'retrieval'; template?:string; dataset_version_id:string;target:{benchmark:string;model:string;prompt:string;knowledge_bases?:Array<{name?:string;selection:LlmSelection}>;retrieval_reranker?:{model?:string;selection?:LlmSelection}|null;dify_retrieval_defaults?:Array<{selection_id?:string|null;knowledge_base_name?:string|null;model?:DifyRetrievalModel|null}>;retrieval_top_k?:number;retrieval_candidate_top_k?:number;retrieval_score_threshold?:number};result_judge:{model:string;prompt:string}|null;scoring_engine:{id:string};metrics:Metric[]};
export type MetricScoreSummary = {k?:number;mean:number|null;scored:number;threshold:Metric['threshold'];implementation_version:string};
export type Evaluation = {evaluation_type?:'rag'|'retrieval';id:string;name:string;created_at:string;status:string;queue_state:string;case_count:number;error:string|null;attempt:number;execution_available?:boolean;cases:CaseResult[];metric_results:MetricResult[];evidence:Evidence;usage: Record<string,{requests:number;output_tokens:number|null}>;summary?:{counts:Record<string,number>;p95_ms:number|null;metrics?:Record<string,MetricScoreSummary>}};
export type EvaluationSummary = Pick<Evaluation, 'id'|'name'|'created_at'|'status'|'queue_state'|'case_count'|'error'|'attempt'|'execution_available'|'evaluation_type'> & {dataset_id?:string|null;dataset_name?:string|null;dataset_version_id?:string|null;retrieval_parameters?:{top_k?:number|null;candidate_top_k?:number|null;score_threshold?:number|null;reranker?:string|null;fusion?:boolean;dify_defaults?:Array<{selection_id?:string|null;knowledge_base_name?:string|null;model?:{search_method?:string;reranking_enable?:boolean;reranking_mode?:string;reranking_model?:{provider_name?:string;model?:string;model_name?:string;reranking_provider_name?:string;reranking_model_name?:string;name?:string}|string|null;reranking_provider_name?:string;reranking_model_name?:string;top_k?:number;score_threshold_enabled?:boolean;score_threshold?:number}|null}>|null}|null;summary?:{metrics?:Record<string,MetricScoreSummary>}};
export type Version = {id:string;local_dataset_id:string;created_at:string;content_hash:string;samples:Array<{id:string;input:string;expected_output?:string;context?:string[];annotations?:Record<string,unknown>;scenario?:unknown}>;dataset:{name:string}};
export type EvaluationCreate = {evaluation_type:'rag'|'retrieval';dataset_id?:string;dataset_version_id?:string;name:string;idempotency_key:string;template:string;sample_limit:number|null;sample_kinds?:string[];target:{benchmark:string;selection:LlmSelection;prompt:string;max_tokens:number;retrieval_top_k?:number;selections?:LlmSelection[];retrieval_reranker?:LlmSelection|null;retrieval_candidate_top_k?:number;retrieval_score_threshold?:number};scoring_engine:string;judge:LlmSelection|null;metrics:Array<{id:string;threshold:number;criteria?:string;examples?:string;k?:number}>;budget?:{max_requests?:number;max_output_tokens?:number};use_cache:boolean};
export type EvaluationConfig = {
  evaluation_type: 'rag'|'retrieval';
  benchmark: string;
  template: string;
  sample_limit: number|null;
  target: EvaluationCreate['target'];
  scoring_engine: string;
  judge: LlmSelection|null;
  metrics: EvaluationCreate['metrics'];
};
export async function evaluationCall<T>(path:string, method='GET', value?:unknown):Promise<T>{
 const response=await authenticatedFetch('/api/'+path,{method,headers:value===undefined?undefined:{'Content-Type':'application/json'},body:value===undefined?undefined:JSON.stringify(value)});
 if(!response.ok){
  const body=await response.json().catch(()=>null);
  const detail=typeof body?.detail==='string'
    ? body.detail
    : Array.isArray(body?.detail)
      ? body.detail.map((item:unknown)=>typeof item==='object'&&item!==null&&'msg' in item?String((item as {msg?:unknown}).msg):String(item)).join('；')
      : '';
  throw new Error(detail || `Request failed (${response.status})`)
 }
 return response.json() as Promise<T>;
}
export const evaluationApi={
 capabilities:()=>evaluationCall<{metrics:Metric[]}>('evaluation-capabilities'),
 list:(datasetId?:string)=>evaluationCall<{items:EvaluationSummary[]}>(`evaluation-runs?${new URLSearchParams({limit:'100',...(datasetId?{dataset_id:datasetId}:{})})}`),
 get:(id:string)=>evaluationCall<Evaluation>('evaluation-runs/'+id),
 delete:(id:string)=>evaluationCall<{deleted:boolean}>(`evaluation-runs/${id}`,'DELETE'),
  bulkDelete:(ids:string[])=>evaluationCall<{succeeded:string[];skipped:string[];failed:{id:string;message:string}[]}>(`evaluation-runs/bulk`,'POST',{ids,action:'delete'}),
 version:(id:string)=>evaluationCall<Version>('dataset-versions/'+id),
 create:(value:EvaluationCreate)=>evaluationCall<Evaluation>('evaluation-runs','POST',value),
 start:(id:string)=>evaluationCall<Evaluation>(`evaluation-runs/${id}/start`,'POST'),
 rerun:(id:string)=>evaluationCall<Evaluation>(`evaluation-runs/${id}/rerun`,'POST'),
 cancel:(id:string)=>evaluationCall<Evaluation>(`evaluation-runs/${id}/cancel`,'POST'),
 retry:(id:string)=>evaluationCall<Evaluation>(`evaluation-runs/${id}/retry-failed`,'POST'),
};
