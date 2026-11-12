import type {Evidence, EvaluationSummary} from './evaluationApi';
export type EvaluationType = 'rag' | 'retrieval';
export type DatasetPurpose = EvaluationType | 'both';
export const evaluationTypeLabels = {rag:'RAG 评测', retrieval:'检索评测'};
export const datasetPurposeLabels = {...evaluationTypeLabels, both:'两者通用'};
export function evaluationType(evidence?:Partial<Evidence>|null):EvaluationType {
  return evidence?.evaluation_type || (evidence?.target?.benchmark === 'dify-retrieve' ? 'retrieval' : 'rag');
}

export function evaluationSummaryType(run:EvaluationSummary & {evidence?:Evidence|null}):EvaluationType|null {
  return run.evaluation_type || (run.evidence ? evaluationType(run.evidence) : null);
}
