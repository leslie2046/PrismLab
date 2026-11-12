import { useId } from 'react';
import { RotateCcw } from 'lucide-react';
import { tr, type Language } from '../i18n';
import { InfoTip } from './InfoTip';
import { WeightedChoices } from './WeightedChoices';

const evolutionLabels: Record<string, [string, string]> = {
  REASONING: ['推理', '让问题需要推导才能作答。例如：根据适用条件判断某项申请能否通过。可能超出资料，建议谨慎开启。'],
  MULTICONTEXT: ['多上下文', '结合多个片段才能回答。例如：综合申请条件和办理流程提问。需要至少两个上下文片段；SDK 在无资料生成时忽略该方式。'],
  CONCRETIZING: ['具体化', '把宽泛的问题变得具体。例如：将“如何申请？”改为“个人首次申请需要哪些材料？”。'],
  CONSTRAINED: ['增加约束', '为问题增加条件或回答要求。例如：“请分三步说明办理流程”。'],
  COMPARATIVE: ['比较', '让问题比较不同对象。例如：“方案 A 与方案 B 的适用条件有什么不同？”。'],
  HYPOTHETICAL: ['假设', '加入假设情境。例如：“如果超过办理期限会怎样？”。可能超出资料，建议谨慎开启。'],
  IN_BREADTH: ['拓宽主题', '从原问题延伸到相关主题。例如：从申请流程扩展到后续维护。可能偏离资料范围，建议谨慎开启。'],
};

export function validEvolutionSettings(value: { generation_backend: string; evolutions: Record<string, number>; num_evolutions: number }, scratch: boolean) {
  return value.generation_backend !== 'deepeval' || Object.entries(value.evolutions).some(([key, weight]) =>
    weight > 0 && (!scratch || value.num_evolutions === 0 || key !== 'MULTICONTEXT'));
}

export function EvolutionSettings({ weights, rounds, scratch, disabled, language, onChange }: {
  weights: Record<string, number>; rounds: number; scratch: boolean; disabled: boolean; language: Language;
  onChange: (weights: Record<string, number>) => void;
}) {
  const t = (text: string) => tr(language, text);
  const id = useId();
  const inactive = rounds === 0;
  const valid = validEvolutionSettings({ generation_backend: 'deepeval', evolutions: weights, num_evolutions: rounds }, scratch);
  return <div className="dataset-evolution-settings" role="group" aria-labelledby={`${id}-title`} aria-describedby={`${id}-help`}>
    <div className="dataset-distribution-heading">
      <span className="label-with-tip" id={`${id}-title`}>{t('演化方式')}<InfoTip label={t('演化方式说明')} text={t('每轮按相对权重抽样一种改写方式，可连续多轮选中同一种。占比 = 该权重 ÷ 启用方式的权重总和，不代表最终样本条数。例如权重 2 和 1，分别约为 66.7% 和 33.3%；全部同为 100 与全部同为 1 的效果相同。')} /></span>
      <button type="button" className="ghost compact" disabled={disabled || inactive} onClick={() => onChange({ CONCRETIZING: 1, CONSTRAINED: 1, COMPARATIVE: 1 })}><RotateCcw aria-hidden="true" />{t('恢复默认')}</button>
    </div>
    <p className="field-help" id={`${id}-help`}>{t(inactive ? '演化轮数为 0，当前不执行改写；已保留各项权重。' : '每轮选择一种改写；调整权重时，抽样占比自动更新。')}</p>
    <WeightedChoices weights={weights} disabled={disabled} inactive={inactive} language={language} heading={t('改写方式')}
      options={Object.entries(evolutionLabels).map(([key, [label, help]]) => ({ key, label: t(label), help: t(help), unavailable: scratch && key === 'MULTICONTEXT' }))} onChange={onChange} />
    {!valid && <small className="error-text" role="alert">{t(scratch && Object.values(weights).some(weight => weight > 0) ? '按任务生成需启用至少一种非多上下文方式。' : '请至少启用一种演化方式，并设置大于 0 的权重。')}</small>}
  </div>;
}
