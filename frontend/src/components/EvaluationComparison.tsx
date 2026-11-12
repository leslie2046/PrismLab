import {useEffect, useMemo, useState} from 'react';
import {ArrowDownRight, ArrowRight, ArrowUpRight, ChevronRight, Minus, Search} from 'lucide-react';
import {authenticatedFetch} from '../api';
import {evaluationApi, type Evaluation, type EvaluationSummary, type MetricResult, type Version} from '../evaluationApi';
import {comparisonDifferences, comparisonOverview, scoreChange, type Change, type Comparison} from '../evaluationComparison';
import {evaluationLabels} from '../evaluationPresentation';
import {evaluationSummaryType, evaluationType} from '../evaluationTypes';
import {tr, type Language} from '../i18n';
import {metricNames} from './EvaluationDialog';
import {InfoTip} from './InfoTip';

const changeLabels: Record<Change, string> = {improved: '提高', declined: '下降', unchanged: '基本不变', unscored: '无法比较'};
const score = (value: number | null) => value == null ? '—' : value.toFixed(3);
function ChangeValue({delta, language}: {delta: number | null; language: Language}) {
  const change = scoreChange(delta), Icon = change === 'improved' ? ArrowUpRight : change === 'declined' ? ArrowDownRight : Minus;
  return <span className="evaluation-comparison-change" data-change={change}><Icon aria-hidden="true"/>{tr(language, changeLabels[change])}{change === 'improved' || change === 'declined' ? ` ${delta! > 0 ? '+' : ''}${delta!.toFixed(3)}` : ''}</span>;
}
function ResultScore({result, value, language}: {result: MetricResult | null; value: number | null; language: Language}) {
  return <>{score(value)}{value == null && <small>{tr(language, result ? evaluationLabels[result.status === 'completed' ? 'unscored' : result.status] || result.status : '未参与此项评分')}</small>}</>;
}

export function EvaluationComparison({run, runs, version, language}: {run: Evaluation; runs: EvaluationSummary[]; version: Version | null; language: Language}) {
  const t = (text: string) => tr(language, text);
  const [baseline, setBaseline] = useState(''), [previous, setPrevious] = useState<Evaluation | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null), [loading, setLoading] = useState(false), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all');
  const candidates = runs.filter(item => item.id !== run.id && evaluationSummaryType(item) === evaluationType(run.evidence) && !['prepared', 'running'].includes(item.status));
  const currentFinished = !['prepared', 'running'].includes(run.status);
  const date = (value: string) => new Date(value).toLocaleString(language, {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setComparison(null); setPrevious(null); setError(''); setFilter('all'); setQuery('');
    if (!baseline) { setLoading(false); return () => { active = false; controller.abort(); }; }
    setLoading(true);
    void (async () => {
      try {
        const old = await evaluationApi.get(baseline);
        if (!active) return;
        setPrevious(old);
        if (old.evidence.dataset_version_id !== run.evidence.dataset_version_id) throw new Error(t('两次评测使用的数据快照不同，无法逐题对齐。请基于同一数据集再次评测后对比。'));
        const response = await authenticatedFetch(`/api/evaluation-runs/${run.id}/compare?baseline_id=${encodeURIComponent(baseline)}`, {signal: controller.signal});
        const value = await response.json();
        if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : t('读取对比结果失败，请重试。'));
        if (active) setComparison(value);
      } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : String(reason)); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; controller.abort(); };
  }, [baseline, run.id, run.evidence.dataset_version_id, retry, language]);
  const overview = useMemo(() => comparison ? comparisonOverview(comparison, run) : null, [comparison, run]);
  const samples = useMemo(() => new Map(version?.samples.map(sample => [sample.id, sample.input]) || []), [version]);
  const incompatibilities = previous ? comparisonDifferences(run, previous) : [];
  const visible = overview?.rows.filter(row => (filter === 'all' || row.change === filter) && (!query.trim() || `${samples.get(row.sample_id) || ''} ${row.sample_id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())))
    .sort((a,b) => (a.delta == null ? b.delta == null ? 0 : 1 : b.delta == null ? -1 : a.delta - b.delta)) || [];
  function choose(value: string) { setComparison(null); setPrevious(null); setError(''); setLoading(!!value); setBaseline(value); }
  return <section className="evaluation-comparison" aria-label={t('历史评测对比')}>
    <div className="evaluation-section-title"><h3>{t('与历史评测对比')}</h3></div>
    <p className="evaluation-guidance">{t('选择一次历史评测，查看同一批问题在本次评测中哪些指标提高、哪些下降。')}</p>
    <div className="evaluation-comparison-picker"><label className="field"><span className="label-with-tip">{t('选择历史评测')}<InfoTip label={t('对比条件说明')} text={t('两次评测需要使用同一数据快照、同一批样本和相同评分配置。选择历史评测后自动读取已保存的结果，不会重新调用模型。')}/></span>
      <select aria-label={t('选择历史评测')} value={baseline} disabled={!currentFinished || !candidates.length} onChange={event => choose(event.target.value)}><option value="">{t('请选择一次历史评测')}</option>{candidates.map(item => <option key={item.id} value={item.id}>{item.name} · {date(item.created_at)} · {t(evaluationLabels[item.status] || item.status)} · {item.id.slice(0,8)}</option>)}</select>
    </label></div>
    {!currentFinished ? <p className="evaluation-comparison-empty">{t('本次评测结束后，即可与历史结果对比。')}</p> : !candidates.length ? <p className="evaluation-comparison-empty">{t('暂无同类型的历史评测。使用同一数据集再完成一次评测，即可查看变化。')}</p> : !baseline && <p className="evaluation-comparison-empty">{t('例如：用同一批问题测试优化前后的 RAG，选择优化前的评测作为参照。')}</p>}
    {previous && <div className="evaluation-comparison-runs"><div><small>{t('历史评测')}</small><strong>{previous.name}</strong><span>{date(previous.created_at)} · {previous.id.slice(0,8)}</span></div><ArrowRight aria-hidden="true"/><div><small>{t('本次评测')}</small><strong>{run.name}</strong><span>{date(run.created_at)} · {run.id.slice(0,8)}</span></div></div>}
    {loading && <p role="status" className="evaluation-guidance">{t('正在读取两次评测的结果…')}</p>}
    {error && <div className="alert" role="alert">{error}<button type="button" className="ghost compact" onClick={() => setRetry(value => value + 1)}>{t('重试')}</button></div>}
    {comparison && overview && <>
      {!comparison.compatible && <div className="evaluation-comparison-warning" role="status"><strong>{t('评分条件不同，暂不判断变好或变差。')}</strong><ul>{(incompatibilities.length ? incompatibilities : ['评分配置不同']).map(reason => <li key={reason}>{t(reason)}</li>)}</ul><p>{t('请保持数据快照、问题范围、评分指标和评分模型一致后再比较。下方仅展示原始分数。')}</p></div>}
      {comparison.compatible && <>
        <div className="evaluation-comparison-verdict" role="status"><strong>{overview.metrics.some(item => item.paired) ? t('可比较指标的变化') : t('暂时没有双方都评分成功的样本，无法判断变化。')}</strong>
          {overview.metrics.some(item => item.paired) && <div>{(['improved', 'declined', 'unchanged'] as const).map(change => <span key={change} data-change={change}>{t(changeLabels[change])} <b>{overview.metrics.filter(item => item.change === change).length}</b> {t('项')}</span>)}</div>}
        </div>
        <div className="evaluation-section-title"><h4>{t('各指标均分变化')}</h4><InfoTip label={t('对比均分说明')} text={t('均分仅统计两次都评分成功的同一批样本，缺失或失败不计为零。差值为本次减去历史得分，按三位小数显示；四舍五入为零时显示基本不变。所有指标均为越高越好。')}/></div>
        <div className="evaluation-table-scroll" tabIndex={0} aria-label={t('指标均分对比表')}><table className="data-table evaluation-comparison-metrics"><thead><tr>{['指标', '历史均分', '本次均分', '变化', '可比较样本'].map(label => <th key={label} scope="col">{t(label)}</th>)}</tr></thead><tbody>{overview.metrics.map(item => <tr key={item.metric.id}><th scope="row">{t(metricNames[item.metric.id] || item.metric.id)}</th><td>{score(item.before)}</td><td>{score(item.after)}</td><td><ChangeValue delta={item.delta} language={language}/></td><td>{item.paired} / {run.case_count}</td></tr>)}</tbody></table></div>
        {!overview.metrics.length && <p className="evaluation-guidance">{t('本次评测未配置评分指标。')}</p>}
        <p className="evaluation-guidance evaluation-comparison-footnote">{t('均分仅统计双方均评分成功的同一批样本。')}</p>
      </>}
      <p className="evaluation-comparison-latency"><span className="label-with-tip">{t('响应耗时（P95）')}<InfoTip label={t('响应耗时说明')} text={t('95% 的应用请求在此时间内完成，越短越快。这里只比较应用响应耗时，不包含评分耗时。')}/></span><span>{t('历史评测')} {comparison.baseline.p95_ms ?? '—'} ms</span><ArrowRight aria-hidden="true"/><span>{t('本次评测')} {comparison.current.p95_ms ?? '—'} ms</span></p>
      <details className="evaluation-comparison-details"><summary><ChevronRight aria-hidden="true"/>{t('查看逐题得分变化')}<small>{t('优先展示下降的评分')}</small></summary>
        <div className="evaluation-comparison-filters"><label className="evaluation-search"><Search aria-hidden="true"/><input aria-label={t('搜索对比问题')} placeholder={t('搜索对比问题')} value={query} onChange={event => setQuery(event.target.value)}/></label><label className="field"><span>{t('变化筛选')}</span><select aria-label={t('变化筛选')} value={filter} onChange={event => setFilter(event.target.value)}><option value="all">{t('全部变化')}</option>{Object.entries(changeLabels).map(([value,label]) => <option key={value} value={value}>{t(label)}</option>)}</select></label></div>
        <div className="evaluation-table-scroll" tabIndex={0} aria-label={t('逐题评分对比表')}><table className="data-table evaluation-comparison-samples"><thead><tr>{['问题', '指标', '历史得分', '本次得分', '变化'].map(label => <th scope="col" key={label}>{t(label)}</th>)}</tr></thead><tbody>{visible.map(row => <tr key={`${row.sample_id}:${row.metric_id}`}><td>{samples.get(row.sample_id) || row.sample_id}</td><td>{t(metricNames[row.metric_id] || row.metric_id)}</td><td><ResultScore result={row.baseline} value={row.before} language={language}/></td><td><ResultScore result={row.current} value={row.after} language={language}/></td><td><ChangeValue delta={row.delta} language={language}/></td></tr>)}</tbody></table></div>
        {!visible.length && <p className="evaluation-guidance">{t('没有符合条件的评分记录。')}</p>}
      </details>
    </>}
  </section>;
}
