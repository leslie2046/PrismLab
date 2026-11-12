import {CheckCircle2, Circle, CircleDashed, XCircle, AlertCircle} from 'lucide-react';
import {tr, type Language} from '../i18n';
import {evaluationLabels} from '../evaluationPresentation';

export function EvaluationStatus({status,language}:{status:string;language:Language}) {
  const success=['completed','passed'].includes(status);
  const failure=['failed','not_passed','target_error','judge_error'].includes(status);
  const warning=['partial_failed','missing_data','unscored'].includes(status);
  const Icon=success?CheckCircle2:failure?XCircle:warning?AlertCircle:['pending','running','prepared'].includes(status)?CircleDashed:Circle;
  return <span className="evaluation-status" data-tone={success?'success':failure?'failure':warning?'warning':'neutral'}><Icon aria-hidden="true"/>{tr(language,evaluationLabels[status]||status)}</span>;
}
