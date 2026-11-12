import { authenticatedFetch } from './api';
import type { LlmSelection } from './types';
export type Metric = { id: string; engine: string; evaluation_types?: string[]; required_fields: string[]; executor_available: boolean; sample_kinds:string[]; threshold: {default:number;value?:number;operator?:'gte'}; implementation_version:string };
export type CaseResult = {id:string; sample_id:string; status:string; error:string|null; observation: {actual_output:string|null; retrieval_context:string[]|null; retrieved_chunk_ids?:string[]|null; duration_ms:number|null; metadata:unknown; turns?:unknown; tools_called?:unknown}|null};
export type MetricResult = {id:string;case_id:string;metric_id:string;status:string;score:number|null;passed:boolean|null;reason:string|null;error:string|null;dependency:{missing_fields:string[];awaiting_fields:string[]};usage?:{cache_hit?:boolean}};
export type Evidence = {evaluation_type?:'rag'|'retrieval'; template?:string; dataset_version_id:string;target:{benchmark:string;model:string;prompt:string;knowledge_bases?:Array<{name?:string;selection:LlmSelection}>;retrieval_reranker?:{model:string}|null;retrieval_top_k?:number;retrieval_candidate_top_k?:number;retrieval_score_threshold?:number};result_judge:{model:string;prompt:string}|null;scoring_engine:{id:string};metrics:Metric[]};
export type MetricScoreSummary = {mean:number|null;scored:number;threshold:Metric['threshold'];implementation_version:string};
export type Evaluation = {evaluation_type?:'rag'|'retrieval';id:string;name:string;created_at:string;status:string;queue_state:string;case_count:number;error:string|null;attempt:number;execution_available?:boolean;cases:CaseResult[];metric_results:MetricResult[];evidence:Evidence;usage: Record<string,{requests:number;output_tokens:number|null}>;summary?:{counts:Record<string,number>;p95_ms:number|null;metrics?:Record<string,MetricScoreSummary>}};
export type EvaluationSummary = Pick<Evaluation, 'id'|'name'|'created_at'|'status'|'queue_state'|'case_count'|'error'|'attempt'|'execution_available'|'evaluation_type'> & {dataset_id?:string|null;dataset_name?:string|null;dataset_version_id?:string|null};
export type Version = {id:string;local_dataset_id:string;created_at:string;content_hash:string;samples:Array<{id:string;input:string;expected_output?:string;context?:string[];annotations?:Record<string,unknown>;scenario?:unknown}>;dataset:{name:string}};
export type EvaluationCreate = {evaluation_type:'rag'|'retrieval';dataset_id?:string;dataset_version_id?:string;name:string;idempotency_key:string;template:string;sample_limit:number|null;sample_kinds?:string[];target:{benchmark:string;selection:LlmSelection;prompt:string;max_tokens:number;retrieval_top_k?:number;selections?:LlmSelection[];retrieval_reranker?:LlmSelection|null;retrieval_candidate_top_k?:number;retrieval_score_threshold?:number};scoring_engine:string;judge:LlmSelection|null;metrics:Array<{id:string;threshold:number;criteria?:string;examples?:string;required_text?:string[];json_schema?:unknown;k?:number}>;budget:{max_requests:number;max_output_tokens:number};use_cache:boolean};
export async function evaluationCall<T>(path:string, method='GET', value?:unknown):Promise<T>{
 const response=await authenticatedFetch('/api/'+path,{method,headers:value===undefined?undefined:{'Content-Type':'application/json'},body:value===undefined?undefined:JSON.stringify(value)});
 if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(typeof body?.detail==='string'?body.detail:`Request failed (${response.status})`)}
 return response.json() as Promise<T>;
}
export const evaluationApi={
 capabilities:()=>evaluationCall<{metrics:Metric[]}>('evaluation-capabilities'),
 list:(datasetId?:string)=>evaluationCall<{items:EvaluationSummary[]}>(`evaluation-runs?${new URLSearchParams({limit:'100',...(datasetId?{dataset_id:datasetId}:{})})}`),
 get:(id:string)=>evaluationCall<Evaluation>('evaluation-runs/'+id),
 delete:(id:string)=>evaluationCall<{deleted:boolean}>(`evaluation-runs/${id}`,'DELETE'),
 version:(id:string)=>evaluationCall<Version>('dataset-versions/'+id),
 create:(value:EvaluationCreate)=>evaluationCall<Evaluation>('evaluation-runs','POST',value),
 start:(id:string)=>evaluationCall<Evaluation>(`evaluation-runs/${id}/start`,'POST'),
 cancel:(id:string)=>evaluationCall<Evaluation>(`evaluation-runs/${id}/cancel`,'POST'),
 retry:(id:string)=>evaluationCall<Evaluation>(`evaluation-runs/${id}/retry-failed`,'POST'),
};
