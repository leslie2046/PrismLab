import { useId } from 'react';
import { tr, type Language } from '../i18n';
import { ragasQueryTypes } from '../ragasDistribution';
import { InfoTip } from './InfoTip';
import { WeightedChoices } from './WeightedChoices';

const queryLabels = {
  single_hop: ['单跳查询', '从一处资料即可找到答案。例如：退款申请的有效期是多少天？'],
  multi_hop_abstract: ['多跳抽象查询', '综合多处资料进行归纳或比较。例如：个人客户与企业客户的退款政策有哪些差异？'],
  multi_hop_specific: ['多跳具体查询', '关联多处资料，回答具体条件下的问题。例如：企业客户购买后第 10 天申请退款，需要哪些材料？'],
};

export function RagasDistributionSettings({ weights, disabled, language, onChange }: {
  weights: Record<string, number>; disabled: boolean; language: Language; onChange: (weights: Record<string, number>) => void;
}) {
  const t = (text: string) => tr(language, text);
  const id = useId();
  return <div className="dataset-evolution-settings dataset-query-distribution" role="group" aria-labelledby={`${id}-title`} aria-describedby={`${id}-help`}>
    <div className="dataset-distribution-heading"><span className="label-with-tip" id={`${id}-title`}>{t('问题类型占比')}<InfoTip label={t('问题类型占比说明')} text={t('按启用类型的相对权重分配样本。权重 2 和 1 对应约 66.7% 和 33.3%，0 表示关闭；无需手动凑足 100。多跳指关联多处资料，不是多轮对话；实际条数会有取整偏差。')} /></span><button type="button" className="ghost compact" disabled={disabled} onClick={() => onChange({ single_hop: 1, multi_hop_abstract: 0, multi_hop_specific: 0 })}>{t('全部单跳')}</button></div>
    <p className="field-help" id={`${id}-help`}>{t('按权重分配问题类型，抽样占比自动计算。')}</p>
    <WeightedChoices weights={weights} disabled={disabled} language={language} heading={t('问题类型')} onChange={onChange}
      options={ragasQueryTypes.map(key => ({ key, label: t(queryLabels[key][0]), help: t(queryLabels[key][1]) }))} />
    {!ragasQueryTypes.some(key => weights[key] > 0) && <small className="error-text" role="alert">{t('请至少启用一种问题类型，并设置大于 0 的权重。')}</small>}
  </div>;
}
