import {useEffect,useId,useMemo,useRef,useState} from 'react';
import {ArrowLeft,Download,Play,Plus,RefreshCw,Search,Square,Trash2} from 'lucide-react';
import type {Catalog,Dataset} from '../types';
import {tr,type Language} from '../i18n';
import {authenticatedFetch} from '../api';
import {useAuth} from '../auth';
import {downloadBlob} from '../datasetApi';
import {evaluationApi,type Evaluation,type EvaluationSummary,type Version} from '../evaluationApi';
import {evaluationType,evaluationSummaryType,evaluationTypeLabels} from '../evaluationTypes';
import {evaluationFailures,evaluationLabels} from '../evaluationPresentation';
import {WorkspaceDialog} from './WorkspaceDialog';
import {EvaluationDialog,metricNames} from './EvaluationDialog';
import {EvaluationScores} from './EvaluationScores';
import {EvaluationSamples} from './EvaluationSamples';
import {EvaluationStatus} from './EvaluationStatus';
import {PageHeader} from './PageHeader';
import {EmptyState} from './EmptyState';
import '../evaluations.css';

const errorText=(error:unknown)=>error instanceof Error?error.message:String(error);
const tabs=[['results','评测结果'],['configuration','运行配置']] as const;
type Tab=typeof tabs[number][0];

function EvaluationResultsVisualization({runs,language}:{runs:EvaluationSummary[];language:Language}) {
  const t=(text:string)=>tr(language,text);
  const metricIds=useMemo(()=>{
    const ids:string[]=[];const seen=new Set<string>();
    runs.forEach(run=>Object.keys(run.summary?.metrics||{}).forEach(id=>{if(!seen.has(id)){seen.add(id);ids.push(id)}}));
    return ids;
  },[runs]);
  if(!runs.length||!metricIds.length)return null;
  const columns=`minmax(190px,1.35fr) repeat(${metricIds.length},minmax(150px,1fr))`;
  return <section className="evaluation-results-visualization" aria-labelledby="evaluation-results-visualization-title">
    <div className="evaluation-results-visualization-head"><div><h2 id="evaluation-results-visualization-title">{t('评测结果可视化')}</h2><p>{t('汇总当前筛选结果的指标均分，分数范围 0–1。')}</p></div><span>{runs.length} {t('条评测')}</span></div>
    <div className="evaluation-results-chart-scroll">
      <div className="evaluation-results-chart" role="table" aria-label={t('所有评测指标均分')}>
        <div className="evaluation-results-chart-row evaluation-results-chart-header" role="row" style={{gridTemplateColumns:columns}}><span role="columnheader">{t('评测')}</span>{metricIds.map(id=><span role="columnheader" key={id}>{t(metricNames[id]||id)}</span>)}</div>
        {runs.map(run=><div className="evaluation-results-chart-row" role="row" style={{gridTemplateColumns:columns}} key={run.id}>
          <div className="evaluation-results-chart-label" role="rowheader"><strong>{run.name||run.id.slice(0,8)}</strong><small>{t(evaluationLabels[run.status]||run.status)} · {new Date(run.created_at).toLocaleDateString(language)}</small></div>
          {metricIds.map(id=>{const score=run.summary?.metrics?.[id]?.mean;return <div className="evaluation-results-chart-cell" role="cell" key={id} aria-label={`${t(metricNames[id]||id)} ${score==null?t('暂无评分'):score.toFixed(3)}`}><div className="evaluation-results-chart-track"><span style={{width:score==null?'0%':`${Math.max(0,Math.min(1,score))*100}%`}}/></div><strong>{score==null?'—':score.toFixed(3)}</strong></div>})}
        </div>)}
      </div>
    </div>
  </section>;
}

export function Evaluations({catalog,language,dataset,onDatasets}:{catalog:Catalog|null;language:Language;dataset?:Dataset;onDatasets?:()=>void}) {
  const t=(text:string)=>tr(language,text),tabId=useId();
  const {canManage}=useAuth();
  const cancelDelete=useRef<HTMLButtonElement>(null);
  const [deleting,setDeleting]=useState<EvaluationSummary|null>(null),[deleteBusy,setDeleteBusy]=useState(false),[deleteError,setDeleteError]=useState('');
  const [runs,setRuns]=useState<EvaluationSummary[]>([]),[selected,setSelected]=useState<Evaluation|null>(null),[version,setVersion]=useState<Version|null>(null);
  const [runId,setRunId]=useState<string|null>(null),[create,setCreate]=useState(false),[exporting,setExporting]=useState(false),[format,setFormat]=useState('json');
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[loading,setLoading]=useState(true),[detailLoading,setDetailLoading]=useState(false),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
  const [typeFilter,setTypeFilter]=useState('all'),[runStatus,setRunStatus]=useState('all'),[query,setQuery]=useState(''),[tab,setTab]=useState<Tab>('results');
  useEffect(()=>{if(deleting)cancelDelete.current?.focus()},[deleting]);
  const summaryLabel=(run:EvaluationSummary)=>{const type=evaluationSummaryType(run);return t(type?evaluationTypeLabels[type]:'评测类型未知')};
  useEffect(()=>{setTab('results')},[runId]);
  useEffect(()=>{
    let active=true;setError('');setDetailLoading(!!runId);
    evaluationApi.list(dataset?.id).then(value=>{if(active)setRuns(value.items)}).catch(error=>{if(active)setError(errorText(error))}).finally(()=>{if(active)setLoading(false)});
    if(runId)evaluationApi.get(runId).then(async value=>{
      const snapshot=await evaluationApi.version(value.evidence.dataset_version_id);
      if(active){setSelected(value);setVersion(snapshot)}
    }).catch(error=>{if(active)setError(errorText(error))}).finally(()=>{if(active)setDetailLoading(false)});
    return()=>{active=false};
  },[runId,reload,dataset?.id]);
  useEffect(()=>{
    if(!selected||!['prepared','running'].includes(selected.status)||!selected.queue_state)return;
    const timer=setInterval(()=>setReload(value=>value+1),2000);return()=>clearInterval(timer);
  },[selected?.status,selected?.queue_state]);
  async function act(work:()=>Promise<unknown>) {
    setBusy(true);setError('');try{await work();setReload(value=>value+1)}catch(error){setError(errorText(error))}finally{setBusy(false)}
  }
  function openRun(id:string) {setNotice('');setRunId(id);setSelected(null);setVersion(null);setError('')}
  function deleteAction(run:EvaluationSummary,compact=true) {
    if(!canManage)return null;
    const active=run.status==='running'||(run.status==='prepared'&&!!run.queue_state);
    const label=t(active?'请先取消排队中或执行中的评测，再删除记录':'删除评测记录');
    return <span title={label}><button type="button" className={compact?'icon-button danger':'ghost danger'} disabled={active||busy||deleteBusy} aria-label={`${t('删除评测记录')} · ${run.name||run.id}`} onClick={()=>{setDeleting(run);setDeleteError('')}}><Trash2 aria-hidden="true"/>{!compact&&t('删除评测记录')}</button></span>;
  }
  async function confirmDelete() {
    if(!deleting)return;
    setDeleteBusy(true);setDeleteError('');
    try {
      await evaluationApi.delete(deleting.id);
      setRuns(value=>value.filter(run=>run.id!==deleting.id));
      if(runId===deleting.id){setRunId(null);setSelected(null);setVersion(null)}
      setDeleting(null);setNotice(t('评测记录已删除'));setReload(value=>value+1);
    }catch(error){setDeleteError(t(errorText(error)))}finally{setDeleteBusy(false)}
  }
  const visibleRuns=runs.filter(run=>(typeFilter==='all'||evaluationSummaryType(run)===typeFilter)&&(runStatus==='all'||run.status===runStatus)&&(!query.trim()||`${run.name} ${run.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  return <section className={`panel evaluation-panel ${dataset ? 'dataset-evaluations' : ''}`}>
    <PageHeader title={selected?.name||t(runId?'评测详情':'评测记录')} description={selected&&version?`${t('数据集')} · ${version.dataset.name}`:t(dataset?'基于当前数据集发起评测，每次评测独立保存指标得分与样本结果。':'选择 RAG 评测检查回答质量，或选择检索评测检查召回与排序。')} breadcrumb={runId&&<button className="evaluation-back" onClick={()=>{setNotice('');setRunId(null);setSelected(null);setVersion(null)}}><ArrowLeft/>{t(dataset?'评测记录':'所有评测')}</button>} actions={<div className="dataset-actions evaluation-toolbar">
        <button className="ghost" disabled={loading||busy} onClick={()=>setReload(value=>value+1)}><RefreshCw/>{t('刷新')}</button>
        {!runId&&<label className="field"><select aria-label={t('筛选评测类型')} value={typeFilter} onChange={event=>setTypeFilter(event.target.value)}><option value="all">{t('全部类型')}</option>{Object.entries(evaluationTypeLabels).map(([value,label])=><option key={value} value={value}>{t(label)}</option>)}</select></label>}
        {!runId&&dataset&&<button className="primary compact" disabled={!!dataset && (!dataset.item_count || dataset.status==='generating')} title={dataset && (!dataset.item_count || dataset.status==='generating') ? t(dataset.status==='generating' ? '数据集构建完成后可发起评测' : '请先添加样本再发起评测') : undefined} onClick={()=>setCreate(true)}><Plus/>{t(dataset?'发起评测':'新建评测')}</button>}
        {!dataset&&onDatasets&&<button className="primary compact" onClick={onDatasets}>{t('前往数据集发起评测')}</button>}
        {selected&&<button className="ghost" onClick={()=>setExporting(true)}><Download/>{t('导出结果')}</button>}
</div>} />
    {error&&<div className="alert" role="alert">{error}</div>}{notice&&<p role="status">{notice}</p>}
    {(runId ? detailLoading&&!selected : loading)&&<p role="status">{t('加载中…')}</p>}
    {!runId&&!loading&&<>
      <div className="evaluation-list-toolbar">
        <label className="evaluation-search"><Search aria-hidden="true"/><input aria-label={t('搜索评测名称或 ID')} placeholder={t('搜索评测名称或 ID')} value={query} onChange={event=>setQuery(event.target.value)}/></label>
        <label className="field"><span>{t('评测状态')}</span><select aria-label={t('筛选评测状态')} value={runStatus} onChange={event=>setRunStatus(event.target.value)}><option value="all">{t('全部状态')}</option>{['prepared','running','completed','partial_failed','failed','cancelled'].map(status=><option value={status} key={status}>{t(evaluationLabels[status])}</option>)}</select></label>
        <span>{t('显示')} {visibleRuns.length} / {runs.length}</span>
      </div>
      <EvaluationResultsVisualization runs={visibleRuns} language={language}/>
      <div className="evaluation-table-scroll evaluation-run-list" tabIndex={0} aria-label={t('评测运行列表')}><table className="data-table"><thead><tr>{['名称','评测类型','状态','样本数','创建时间',...(canManage?['操作']:[])].map(label=><th scope="col" key={label}>{t(label)}</th>)}</tr></thead><tbody>{visibleRuns.map(run=><tr key={run.id}><td><button className="text-button" onClick={()=>openRun(run.id)}>{run.name||run.id.slice(0,12)}</button><small className="evaluation-run-id">{run.id.slice(0,8)}</small></td><td>{summaryLabel(run)}</td><td><EvaluationStatus status={run.status} language={language}/></td><td>{run.case_count}</td><td>{new Date(run.created_at).toLocaleString(language)}</td>{canManage&&<td className="evaluation-delete-cell">{deleteAction(run)}</td>}</tr>)}</tbody></table></div>
      {!visibleRuns.length&&<EmptyState title={t(runs.length?'没有匹配的评测':dataset?'暂无评测记录。发起评测后，可在这里查看指标得分与样本结果。':'尚无评测。选择 RAG 评测或检索评测，先试跑少量样本。')} />}
    </>}
    {selected?.evidence&&<>
      <div className="evaluation-summary"><EvaluationStatus status={selected.status} language={language}/><span>{t(evaluationTypeLabels[evaluationType(selected.evidence)])}</span><span>{selected.case_count} {t('样本')}</span><span>{selected.evidence.scoring_engine.id==='ragas'?'RAGAS':selected.evidence.scoring_engine.id==='deepeval'?'DeepEval':t('规则')}</span>{!!selected.evidence.target.knowledge_bases?.length&&<span>{selected.evidence.target.knowledge_bases.length} {t('个知识库')}</span>}{selected.evidence.target.model&&<span>{selected.evidence.target.model}</span>}<span>{t('P95 耗时')} {selected.summary?.p95_ms==null?'—':`${selected.summary.p95_ms} ms`}</span></div>
      <div className="dataset-actions evaluation-run-actions">
        {deleteAction(selected,false)}
        {selected.status==='prepared'&&!selected.queue_state&&selected.execution_available===true&&<button className="primary compact" disabled={busy} onClick={()=>void act(async()=>setSelected(await evaluationApi.start(selected.id)))}><Play/>{t('开始评测')}</button>}
        {['prepared','running'].includes(selected.status)&&<button className="ghost" disabled={busy} onClick={()=>void act(async()=>setSelected(await evaluationApi.cancel(selected.id)))}><Square/>{t('取消评测')}</button>}
        {selected.execution_available===true&&['failed','partial_failed'].includes(selected.status)&&<button className="ghost" disabled={busy} onClick={()=>void act(async()=>{await evaluationApi.retry(selected.id);setSelected(await evaluationApi.start(selected.id))})}><RefreshCw/>{t('重试失败项')}</button>}
      </div>
      {selected.execution_available!==true&&<p role="status">{t('这是旧版配置记录，请选择原数据版本新建可执行的评测。')}</p>}
      {selected.error&&<p className="alert" role="alert">{t(evaluationFailures[selected.error]||selected.error)}</p>}
      <div className="evaluation-tabs" role="tablist" aria-label={t('评测详情导航')}>{tabs.map(([value,label],index)=><button key={value} type="button" role="tab" id={`${tabId}-${value}-tab`} aria-selected={tab===value} aria-controls={`${tabId}-${value}-panel`} tabIndex={tab===value?0:-1} onClick={()=>setTab(value)} onKeyDown={event=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();
        const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;
        setTab(tabs[next][0]);event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
      }}>{t(label)}</button>)}</div>
      <div role="tabpanel" id={`${tabId}-${tab}-panel`} aria-labelledby={`${tabId}-${tab}-tab`}>
        {tab==='results'&&<><EvaluationScores run={selected} language={language}/><EvaluationSamples key={selected.id} run={selected} version={version} language={language}/></>}
        {tab==='configuration'&&<>
          {!!selected.evidence.target.knowledge_bases?.length && <section className="evaluation-retrieval-config"><h3 className="evaluation-tab-heading">{t('检索策略')}</h3><p>{selected.evidence.target.knowledge_bases.map(base=>base.name || t('Dify 知识库')).join('、')}</p><dl className="evaluation-counts"><div><dt>{t('重排模型（Reranker）')}</dt><dd>{selected.evidence.target.retrieval_reranker?.model || t('未选择重排模型')}</dd></div><div><dt>{t('每库召回数量')}</dt><dd>{selected.evidence.target.retrieval_candidate_top_k}</dd></div><div><dt>{t('最终 Top K')}</dt><dd>{selected.evidence.target.retrieval_top_k}</dd></div><div><dt>{t('分数阈值（Score threshold）')}</dt><dd>{selected.evidence.target.retrieval_score_threshold ?? 0}</dd></div></dl></section>}
          <h3 className="evaluation-tab-heading">{t('运行统计')}</h3>
          <dl className="evaluation-counts">{['completed','not_passed','target_error','judge_error','missing_data'].map(status=><div key={status}><dt>{t(status==='not_passed'?'未通过':evaluationLabels[status])}</dt><dd>{selected.summary?.counts[status]??0}</dd></div>)}{Object.entries(selected.usage||{}).map(([stage,value])=><div key={stage}><dt>{t(stage==='target'?'应用调用用量':stage==='reranker'?'重排调用用量':'评分调用用量')}</dt><dd>{value.requests} {t('次请求')} · {value.output_tokens??'—'} tokens</dd></div>)}<div><dt>{t('成本')}</dt><dd>{t('成本未知')}</dd></div></dl>
          <details className="evaluation-frozen"><summary>{t('评测快照配置')}</summary><pre>{JSON.stringify(selected.evidence,null,2)}</pre></details>
        </>}
      </div>
    </>}
    {deleting&&<WorkspaceDialog title={t('删除评测记录')} busy={deleteBusy} language={language} onClose={()=>setDeleting(null)}><form className="workspace-dialog-body" onSubmit={event=>{event.preventDefault();void confirmDelete()}}>
      <p>{t('确定删除此评测记录及其全部结果吗？')}</p><strong className="evaluation-delete-name">{deleting.name||deleting.id}</strong>
      <p>{t('此操作无法撤销。原数据集、评测快照及其他评测记录将保留。')}</p>
      {deleteError&&<div className="alert" role="alert">{deleteError}</div>}
      <div className="workspace-dialog-actions"><button type="button" className="ghost" ref={cancelDelete} disabled={deleteBusy} onClick={()=>setDeleting(null)}>{t('取消')}</button><button type="submit" className="primary evaluation-delete-confirm" disabled={deleteBusy}>{t(deleteBusy?'删除中…':'确认删除')}</button></div>
    </form></WorkspaceDialog>}
    {create&&<EvaluationDialog fixedDataset={dataset} catalog={catalog} language={language} onClose={()=>{setCreate(false);setReload(value=>value+1)}} onCreated={run=>{setCreate(false);openRun(run.id);setSelected(run);setReload(value=>value+1)}}/>}
    {exporting&&selected&&<WorkspaceDialog title={t('导出评测结果')} busy={busy} language={language} onClose={()=>setExporting(false)}><form className="workspace-dialog-body" onSubmit={event=>{event.preventDefault();void act(async()=>{const response=await authenticatedFetch(`/api/evaluation-runs/${selected.id}/export?format=${format}`);if(!response.ok)throw new Error(t('导出失败'));downloadBlob(await response.blob(),`evaluation-${selected.id}.${format}`);setExporting(false)})}}><label className="field"><span>{t('文件格式')}</span><select aria-label={t('文件格式')} value={format} onChange={event=>setFormat(event.target.value)}>{['json','jsonl','csv'].map(value=><option key={value}>{value}</option>)}</select></label><p>{t('导出包含冻结参考资料和实际回答，请按空间数据权限保管。')}</p><button className="primary" disabled={busy}>{t('导出')}</button></form></WorkspaceDialog>}
  </section>;
}
