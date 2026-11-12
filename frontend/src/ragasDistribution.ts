export const ragasQueryTypes = ['single_hop', 'multi_hop_abstract', 'multi_hop_specific'] as const;
export type RagasQueryType = typeof ragasQueryTypes[number];
type Distribution = Record<string, number>;

export function validRagasDistribution(value: { generation_backend: string; ragas_query_distribution: Distribution }) {
  return value.generation_backend !== 'ragas_native' || ragasQueryTypes.some(key => value.ragas_query_distribution[key] > 0);
}

function allocate(weights: number[], total: number): number[] {
  const sum = weights.reduce((a, b) => a + b, 0);
  if (!sum) return weights.map((_, index) => index === 0 ? total : 0);
  const exact = weights.map(weight => weight / sum * total);
  const rounded = exact.map(Math.floor);
  const order = exact.map((value, index) => ({index, remainder: value - rounded[index]}))
    .sort((a, b) => b.remainder - a.remainder || a.index - b.index);
  const remaining = total - rounded.reduce((a, b) => a + b, 0);
  for (let i = 0; i < remaining; i++) rounded[order[i].index]++;
  return rounded;
}

// Existing configurations store relative weights; opening the form must not rewrite them.
export function queryPercentages(weights: Distribution): Record<RagasQueryType, number> {
  const values = allocate(ragasQueryTypes.map(key => Math.max(0, weights[key] || 0)), 100);
  return Object.fromEntries(ragasQueryTypes.map((key, index) => [key, values[index]])) as Record<RagasQueryType, number>;
}

export function changeQueryPercentage(weights: Distribution, key: RagasQueryType, next: number): Record<RagasQueryType, number> {
  const percent = Math.max(0, Math.min(100, Math.round(next)));
  const others = ragasQueryTypes.filter(type => type !== key);
  const rest = allocate(others.map(type => Math.max(0, weights[type] || 0)), 100 - percent);
  return {...queryPercentages(weights), [key]: percent, ...Object.fromEntries(others.map((type, index) => [type, rest[index]]))};
}
