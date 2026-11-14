import type {Evaluation} from '../evaluationApi';
import {tr, type Language} from '../i18n';
import {metricOverview, evaluationLabels} from '../evaluationPresentation';
import {metricHelp, metricName} from './EvaluationDialog';
import {InfoTip} from './InfoTip';

export function EvaluationScores({run, language}:{run:Evaluation;language:Language}) {
  const t=(text:string)=>tr(language,text),metrics=metricOverview(run);
  return <section className="evaluation-scores" aria-label={t('本次评测结果')}>
    <div className="evaluation-scores-heading">
      <h3>{t('本次评测结果')}<InfoTip label={t('指标得分说明')} text={t('平均分仅统计本次评测中已完成且有有效分数的样本。通过率为达到该指标阈值的样本数除以有效评分样本数。缺少数据、执行失败和等待中的样本不记为零分，也不参与平均分或通过率；各项指标分别计算，不合并为总分。')}/></h3>
      <span>{t('分数范围 0–1 · 越高越好')}</span>
    </div>
    {!metrics.length?<p className="evaluation-guidance">{t('本次评测未配置评分指标。')}</p>:<>
      <div className="evaluation-metric-cards">{metrics.map(({metric,mean,scored,passed,passRate,threshold,unscored,distribution})=>{
        const distributionText=distribution.map((count,i)=>`${(i/5).toFixed(1)}–${((i+1)/5).toFixed(1)}: ${count}`).join(', ');
        return <article className="evaluation-metric-card" key={metric.id} aria-label={t(metricName(metric))}>
          <header><h4 className="label-with-tip">{t(metricName(metric))}{metricHelp[metric.id]&&<InfoTip label={t('指标说明')} text={t(metricHelp[metric.id])}/>}</h4><span>{metric.id.split('.')[0]==='ragas'?'RAGAS':metric.id.startsWith('deepeval.')?'DeepEval':t('规则')}</span></header>
          {metric.id==='rules.recall_at_k'&&<p className="evaluation-guidance">{t(metric.k_source==='dify'?'K 来自 Dify 知识库配置':metric.k_source==='fusion'?'K 来自多路召回的最终 Top K':'K 为本次评测保存的固定值')}</p>}
          <div className="evaluation-metric-primary"><div><span>{t('平均分')}</span><strong className="evaluation-mean">{mean==null?'—':mean.toFixed(3)}</strong></div><div><span>{t('通过率')}</span><strong>{passRate==null?'—':`${(passRate*100).toFixed(1)}%`}</strong><small>{passed} / {scored} {t('通过')}</small></div></div>
          {scored>0?<div className="evaluation-distribution"><div className="evaluation-distribution-caption"><span>{t('得分分布')}</span><span>{scored} {t('项已评分')}</span></div><div className="evaluation-distribution-list" role="list" aria-label={`${t('得分分布')}: ${distributionText}`}>{distribution.map((count,i)=>{const range=`${(i/5).toFixed(1)}–${((i+1)/5).toFixed(1)}`;const percentage=count/scored*100;return <div className="evaluation-distribution-row" role="listitem" key={i}><span className="evaluation-distribution-range">{range}</span><span className="evaluation-distribution-track" aria-hidden="true"><span style={{width:`${percentage}%`}}/></span><span className="evaluation-distribution-count">{count} {t('项')} <small>({percentage.toFixed(0)}%)</small></span></div>})}</div></div>:<p className="evaluation-no-score">{t('暂无有效评分')}</p>}
          <footer><span>{t('有效评分')} {scored} / {run.case_count}</span><span>{t('通过阈值')} {typeof threshold==='number'?`≥ ${threshold.toFixed(3)}`:'—'}</span></footer>
          {!!Object.keys(unscored).length&&<p className="evaluation-card-warning">{Object.entries(unscored).map(([status,count])=>`${t(evaluationLabels[status]||status)} ${count}`).join(' · ')}</p>}
        </article>;
      })}</div>
    </>}
  </section>;
}
