import type {Evaluation, MetricResult} from './evaluationApi';
import {hasScore} from './evaluationPresentation';

export type Comparison = {
  compatible: boolean;
  baseline: {p95_ms: number | null};
  current: {p95_ms: number | null};
  differences: Array<{sample_id: string; metric_id: string; baseline: MetricResult | null; current: MetricResult; delta: number | null}>;
};
export type Change = 'improved' | 'declined' | 'unchanged' | 'unscored';
export function scoreChange(delta: number | null): Change {
  if (delta == null || !Number.isFinite(delta)) return 'unscored';
  const displayed = Number(delta.toFixed(3));
  return displayed > 0 ? 'improved' : displayed < 0 ? 'declined' : 'unchanged';
}
export function comparisonOverview(comparison: Comparison, run: Evaluation) {
  const rows = comparison.differences.map(row => {
    const before = row.baseline && hasScore(row.baseline) ? row.baseline.score : null;
    const after = hasScore(row.current) ? row.current.score : null;
    const delta = comparison.compatible && before != null && after != null ? after - before : null;
    return {...row, before, after, delta, change: scoreChange(delta)};
  });
  const metrics = run.evidence.metrics.map(metric => {
    // Use matching successful pairs, so unequal failure coverage cannot create an apparent improvement.
    const paired = rows.filter(row => row.metric_id === metric.id && row.delta != null);
    const before = paired.length ? paired.reduce((sum, row) => sum + row.before!, 0) / paired.length : null;
    const after = paired.length ? paired.reduce((sum, row) => sum + row.after!, 0) / paired.length : null;
    const delta = before != null && after != null ? after - before : null;
    return {metric, paired: paired.length, before, after, delta, change: scoreChange(delta)};
  });
  return {rows, metrics};
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>`${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`;
  return JSON.stringify(value) ?? 'null';
}
export function comparisonDifferences(current: Evaluation, previous: Evaluation) {
  const reasons: string[] = [];
  if (canonical(current.evidence.metrics) !== canonical(previous.evidence.metrics)) reasons.push('评分指标、阈值或评分规则不同');
  if (canonical(current.evidence.result_judge) !== canonical(previous.evidence.result_judge)) reasons.push('评分模型或评分提示词不同');
  if (canonical(current.cases.map(item=>item.sample_id).sort()) !== canonical(previous.cases.map(item=>item.sample_id).sort())) reasons.push('参与评测的问题范围不同');
  return reasons;
}
