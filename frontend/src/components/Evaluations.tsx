import {useEffect,useId,useMemo,useRef,useState,type ReactNode} from 'react';
import {ArrowLeft,Download,Play,Plus,RefreshCw,Search,Square,Trash2} from 'lucide-react';
import type {Catalog,Dataset} from '../types';
import {tr,type Language} from '../i18n';
import {authenticatedFetch} from '../api';
import {useAuth} from '../auth';
import {createClientId} from '../clientId';
import {downloadBlob} from '../datasetApi';
import {evaluationApi,type Evaluation,type EvaluationCreate,type EvaluationSummary,type Version} from '../evaluationApi';
import {evaluationType,evaluationSummaryType,evaluationTypeLabels} from '../evaluationTypes';
import {evaluationFailures,evaluationLabels,resultColumns} from '../evaluationPresentation';
import {WorkspaceDialog} from './WorkspaceDialog';
import {metricName} from './EvaluationDialog';
import {EvaluationScores} from './EvaluationScores';
import {EvaluationSamples} from './EvaluationSamples';
import {EvaluationStatus} from './EvaluationStatus';
import {PageHeader} from './PageHeader';
import {EmptyState} from './EmptyState';
import {BulkSelectionBar,SelectionCheckbox} from './BulkSelection';
import {useBulkSelection} from '../bulkSelection';
import {useConfirm} from './ConfirmDialog';
import {Pagination,usePagination} from './Pagination';
import '../evaluations.css';

const errorText=(error:unknown)=>error instanceof Error?error.message:String(error);
const tabs=[['results','评测结果'],['details','评测明细'],['configuration','运行配置']] as const;
type Tab=typeof tabs[number][0];

function difyDefaultParameterSpans(params:NonNullable<EvaluationSummary['retrieval_parameters']>, t:(text:string)=>string) {
  const defaults=params.dify_defaults;
  if (!defaults?.length) return [<span key="dify-default-missing">{t('Dify 默认参数未记录')}</span>];
  return defaults.map((entry,index)=>{
    const model=entry.model;
    if (!model) return <span key={`dify-default-${index}`}>{t('Dify 默认参数读取失败')}</span>;
    const reranker = model.reranking_enable
      ? (model.reranking_model_name || (typeof model.reranking_model === 'string' ? model.reranking_model : model.reranking_model?.reranking_model_name || model.reranking_model?.model_name || model.reranking_model?.model || model.reranking_model?.name || model.reranking_model?.provider_name || model.reranking_model?.reranking_provider_name) || t('已启用'))
      : t('未启用');
    const prefix=defaults.length>1 ? `${entry.knowledge_base_name || `KB${index+1}`} ` : '';
    return <span key={`dify-default-${index}`} title={entry.knowledge_base_name || undefined}>{prefix}{t('Dify默认')} · K {model.top_k ?? '—'} · {t('Score')} {model.score_threshold_enabled ? (model.score_threshold ?? 0) : '0（关闭）'} · {t('Reranker')} {reranker}</span>;
  });
}

function EvaluationResultsVisualization({runs,language,paginationKey,onOpen,deleteAction,canManage,selectedIds,selectionBusy,onToggleSelection,onToggleAll}:{runs:EvaluationSummary[];language:Language;paginationKey:string;onOpen:(id:string)=>void;deleteAction:(run:EvaluationSummary)=>ReactNode;canManage:boolean;selectedIds:Set<string>;selectionBusy:boolean;onToggleSelection:(ids:string[],selected:boolean)=>void;onToggleAll:(selected:boolean)=>void}) {
  const t=(text:string)=>tr(language,text);
  const metrics=useMemo(()=>resultColumns(runs),[runs]);
  const [sort,setSort]=useState<{key:string;direction:'asc'|'desc'}|null>(null);
  const scoreForMetric=(run:EvaluationSummary,metric:typeof metrics[number])=>{
    const summary=run.summary?.metrics?.[metric.id];
    if(['rules.recall_at_k','rules.precision_at_k'].includes(metric.id)&&summary?.k!==metric.k)return null;
    return typeof summary?.mean==='number'&&Number.isFinite(summary.mean)?summary.mean:null;
  };
  const sortedRuns=useMemo(()=>{
    if(!sort)return runs;
    const metric=metrics.find(item=>item.key===sort.key);
    if(!metric)return runs;
    return [...runs].sort((a,b)=>{
      const av=scoreForMetric(a,metric),bv=scoreForMetric(b,metric);
      if(av==null&&bv==null)return 0;
      if(av==null)return 1;
      if(bv==null)return -1;
      return sort.direction==='asc'?av-bv:bv-av;
    });
  },[runs,metrics,sort]);
  const pagination=usePagination(runs.length,paginationKey,10);
  const pageRuns=sortedRuns.slice((pagination.page-1)*pagination.pageSize,pagination.page*pagination.pageSize);
  // Keep each record's sequence from the unsorted list so the number moves with
  // the record when the user sorts by a metric.
  const runNumbers=useMemo(()=>new Map(runs.map((run,index)=>[run.id,index+1])),[runs]);
  function toggleSort(key:string) {
    setSort(current=>current?.key===key
      ? {key,direction:current.direction==='desc'?'asc':'desc'}
      : {key,direction:'desc'});
  }
  if(!runs.length)return null;
  const selectable=runs.filter(run=>!['running'].includes(run.status)&&!(run.status==='prepared'&&!!run.queue_state));
  const allSelected=!!selectable.length&&selectable.every(run=>selectedIds.has(run.id));
  const mixed=!allSelected&&selectable.some(run=>selectedIds.has(run.id));
  return <><section className="evaluation-results-visualization">
    <div className="evaluation-results-table-scroll">
      <table className="evaluation-results-table" aria-label={t('所有评测结果')}>
        <thead><tr>{canManage&&<th className="evaluation-results-select"><SelectionCheckbox checked={allSelected} mixed={mixed} disabled={selectionBusy||!selectable.length} label={t('全选可删除评测')} onChange={onToggleAll}/></th>}<th>{t('序号')}</th>{metrics.map(metric=>{const active=sort?.key===metric.key;return <th key={metric.key} aria-sort={active?(sort.direction==='asc'?'ascending':'descending'):'none'}><button type="button" className="evaluation-sort-button" onClick={()=>toggleSort(metric.key)} aria-label={`${t(metricName(metric))}${active?`，${sort.direction==='asc'?t('升序'):t('降序')}`:''}`}>{t(metricName(metric))}{active&&<span aria-hidden="true"> {sort.direction==='asc'?'↑':'↓'}</span>}</button></th>})}<th>{t('检索参数')}</th><th>{t('评测时间')}</th><th>{t('状态')}</th><th>{t('样本数')}</th><th>{t('操作')}</th></tr></thead>
        <tbody>{pageRuns.map(run=>{const sequence=runNumbers.get(run.id)??'—';const disabled=!selectable.some(item=>item.id===run.id);const params=run.retrieval_parameters;return <tr key={run.id} className={selectedIds.has(run.id)?'bulk-selected':undefined}>{canManage&&<td className="evaluation-results-select"><SelectionCheckbox checked={selectedIds.has(run.id)} disabled={selectionBusy||disabled} label={`${t('选择评测')} ${sequence}`} onChange={value=>onToggleSelection([run.id],value)}/></td>}<td><button type="button" className="text-button evaluation-results-sequence" title={run.name||run.id} onClick={()=>onOpen(run.id)}>#{sequence}</button></td>{metrics.map(metric=>{const score=scoreForMetric(run,metric);return <td className="evaluation-results-score" key={metric.key}>{score==null?'—':score.toFixed(3)}</td>})}<td><div className="evaluation-results-parameters">{params?<>{difyDefaultParameterSpans(params,t)}{params.fusion&&params.top_k!=null&&<span>{t('测试 Top K')} {params.top_k}</span>}{params.reranker&&<span>{t('本次 Reranker')} {params.reranker}</span>}</>:'—'}</div></td><td>{new Date(run.created_at).toLocaleString(language)}</td><td><EvaluationStatus status={run.status} language={language}/></td><td>{run.case_count}</td><td><div className="evaluation-results-actions"><button type="button" className="text-button evaluation-view-result" onClick={()=>onOpen(run.id)}>{t('查看结果')}</button>{canManage&&deleteAction(run)}</div></td></tr>})}</tbody>
      </table>
      {!metrics.length&&<p className="evaluation-guidance">{t('本次评测未配置评分指标。')}</p>}
    </div>
  </section><Pagination {...pagination} total={runs.length} language={language} label={t('评测记录')} pageSizes={[10,20,50]} />
  </>;
}

export function Evaluations({catalog,language,dataset,onDatasets}:{catalog:Catalog|null;language:Language;dataset?:Dataset;onDatasets?:()=>void}) {
  const t=(text:string)=>tr(language,text),tabId=useId();
  const confirm=useConfirm();
  const {canManage}=useAuth();
  const cancelDelete=useRef<HTMLButtonElement>(null);
  const [deleting,setDeleting]=useState<EvaluationSummary|null>(null),[deleteBusy,setDeleteBusy]=useState(false),[deleteError,setDeleteError]=useState('');
  const [runs,setRuns]=useState<EvaluationSummary[]>([]),[selected,setSelected]=useState<Evaluation|null>(null),[version,setVersion]=useState<Version|null>(null);
  const [runId,setRunId]=useState<string|null>(null),[exporting,setExporting]=useState(false),[format,setFormat]=useState('json');
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[loading,setLoading]=useState(true),[detailLoading,setDetailLoading]=useState(false),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
  const [confirmStart,setConfirmStart]=useState(false);
  const [typeFilter,setTypeFilter]=useState('all'),[runStatus,setRunStatus]=useState('all'),[query,setQuery]=useState(''),[tab,setTab]=useState<Tab>('results');
  const evaluationSelection=useBulkSelection(JSON.stringify(['evaluation-list',dataset?.id||'',typeFilter,runStatus,query]));
  useEffect(()=>{if(deleting)cancelDelete.current?.focus()},[deleting]);
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
  async function startConfiguredEvaluation() {
    if (!dataset) return;
    const config = dataset.evaluation_config;
    if (!config) {
      setError(t('请先在数据集设置的评测配置中完成配置，再发起评测。'));
      return;
    }
    setBusy(true); setError(''); setNotice('');
    try {
      const {benchmark: _benchmark, ...storedConfig} = config;
      const payload:EvaluationCreate = {
        ...storedConfig,
        dataset_id:dataset.id,
        sample_kinds:['single_turn'],
        name:`${dataset.name} · ${t(evaluationTypeLabels[config.evaluation_type])}`.slice(0,120),
        idempotency_key:createClientId(),
        use_cache:true,
      };
      const prepared = await evaluationApi.create(payload);
      const started = await evaluationApi.start(prepared.id);
      openRun(started.id);
      setSelected(started);
      setReload(value=>value+1);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
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
  const deletableRuns=(items:EvaluationSummary[])=>items.filter(run=>run.status!=='running'&&!(run.status==='prepared'&&!!run.queue_state));
  async function bulkDelete() {
    const count=evaluationSelection.ids.size;
    if(!count||!await confirm({title:t('确认删除吗？'),message:`${t('批量删除')} ${count} ${t('条评测记录')}`,detail:t('此操作无法撤销。'),confirmLabel:t('确认删除'),danger:true}))return;
    await evaluationSelection.execute(ids=>evaluationApi.bulkDelete(ids),result=>{
      setRuns(value=>value.filter(run=>!result.succeeded.includes(run.id)));
      if(runId&&result.succeeded.includes(runId)){setRunId(null);setSelected(null);setVersion(null);}
      setReload(value=>value+1);
    });
  }
  const visibleRuns=runs.filter(run=>(typeFilter==='all'||evaluationSummaryType(run)===typeFilter)&&(runStatus==='all'||run.status===runStatus)&&(!query.trim()||`${run.name} ${run.id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  const paginationKey=JSON.stringify([dataset?.id||'',typeFilter,runStatus,query]);
  return <section className={`panel evaluation-panel ${runId ? 'evaluation-detail-panel' : 'evaluation-list-panel'} ${dataset ? 'dataset-evaluations' : ''}`}>
    <PageHeader title={selected?.name||t(runId?'评测详情':'评测记录')} description={selected&&version?`${t('数据集')} · ${version.dataset.name}`:t(dataset?'基于当前数据集发起评测，每次评测独立保存指标得分与样本结果。':'选择 RAG 评测检查回答质量，或选择检索评测检查召回与排序。')} breadcrumb={runId&&<button className="evaluation-back" onClick={()=>{setNotice('');setRunId(null);setSelected(null);setVersion(null)}}><ArrowLeft/>{t(dataset?'评测记录':'所有评测')}</button>} actions={<div className="dataset-actions evaluation-toolbar">
        <button className="ghost" disabled={loading||busy} onClick={()=>setReload(value=>value+1)}><RefreshCw/>{t('刷新')}</button>
        {!runId&&dataset&&<button className="primary compact" disabled={!!dataset && (!dataset.item_count || dataset.status==='generating')} title={dataset && (!dataset.item_count || dataset.status==='generating') ? t(dataset.status==='generating' ? '数据集构建完成后可发起评测' : '请先添加样本再发起评测') : undefined} onClick={()=>setConfirmStart(true)}><Plus/>{t(dataset?'发起评测':'新建评测')}</button>}
        {!dataset&&onDatasets&&<button className="primary compact" onClick={onDatasets}>{t('前往数据集发起评测')}</button>}
        {selected&&<button className="ghost" onClick={()=>setExporting(true)}><Download/>{t('导出结果')}</button>}
        {selected&&deleteAction(selected,false)}
        {selected&&selected.execution_available===true&&<button className="ghost" disabled={busy} onClick={()=>void act(async()=>{const rerun=await evaluationApi.rerun(selected.id);const queued=await evaluationApi.start(rerun.id);setRunId(queued.id);setSelected(queued)})}><RefreshCw/>{t('重新评测')}</button>}
        {selected&&selected.status==='prepared'&&!selected.queue_state&&selected.execution_available===true&&<button className="primary compact" disabled={busy} onClick={()=>void act(async()=>setSelected(await evaluationApi.start(selected.id)))}><Play/>{t('开始评测')}</button>}
        {selected&&['prepared','running'].includes(selected.status)&&<button className="ghost" disabled={busy} onClick={()=>void act(async()=>setSelected(await evaluationApi.cancel(selected.id)))}><Square/>{t('取消评测')}</button>}
        {selected&&selected.execution_available===true&&['failed','partial_failed'].includes(selected.status)&&<button className="ghost" disabled={busy} onClick={()=>void act(async()=>{await evaluationApi.retry(selected.id);setSelected(await evaluationApi.start(selected.id))})}><RefreshCw/>{t('重试失败项')}</button>}
</div>} />
    {error&&<div className="alert" role="alert">{error}</div>}{notice&&<p role="status">{notice}</p>}
    {!runId&&!loading&&<>
      <div className="evaluation-list-toolbar">
        <label className="filter-control"><select aria-label={t('筛选评测类型')} value={typeFilter} onChange={event=>setTypeFilter(event.target.value)}><option value="all">{t('全部类型')}</option>{Object.entries(evaluationTypeLabels).map(([value,label])=><option key={value} value={value}>{t(label)}</option>)}</select></label>
        <label className="filter-control"><select aria-label={t('筛选评测状态')} value={runStatus} onChange={event=>setRunStatus(event.target.value)}><option value="all">{t('全部状态')}</option>{['prepared','running','completed','partial_failed','failed','cancelled'].map(status=><option value={status} key={status}>{t(evaluationLabels[status])}</option>)}</select></label>
        <label className="evaluation-search"><Search aria-hidden="true"/><input aria-label={t('搜索评测名称或 ID')} placeholder={t('搜索评测名称或 ID')} value={query} onChange={event=>setQuery(event.target.value)}/></label>
      </div>
      {canManage&&<BulkSelectionBar selection={evaluationSelection} language={language}><button type="button" className="ghost danger" disabled={evaluationSelection.busy||busy||deleteBusy} onClick={()=>void bulkDelete()}><Trash2 aria-hidden="true"/>{t('批量删除')}</button></BulkSelectionBar>}
      <EvaluationResultsVisualization runs={visibleRuns} language={language} paginationKey={paginationKey} onOpen={openRun} deleteAction={deleteAction} canManage={canManage} selectedIds={evaluationSelection.ids} selectionBusy={evaluationSelection.busy||busy||deleteBusy} onToggleSelection={(ids,value)=>evaluationSelection.toggle(ids,value)} onToggleAll={value=>evaluationSelection.toggle(deletableRuns(visibleRuns).map(run=>run.id),value)}/>
      {!visibleRuns.length&&<EmptyState title={t(runs.length?'没有匹配的评测':dataset?'暂无评测记录。发起评测后，可在这里查看指标得分与样本结果。':'尚无评测。选择 RAG 评测或检索评测，先试跑少量样本。')} />}
    </>}
    {selected?.evidence&&<>
      <div className="evaluation-summary"><EvaluationStatus status={selected.status} language={language}/><span>{t(evaluationTypeLabels[evaluationType(selected.evidence)])}</span><span>{selected.case_count} {t('样本')}</span><span>{selected.evidence.scoring_engine.id==='ragas'?'RAGAS':selected.evidence.scoring_engine.id==='deepeval'?'DeepEval':t('规则')}</span>{!!selected.evidence.target.knowledge_bases?.length&&<span>{selected.evidence.target.knowledge_bases.length} {t('个知识库')}</span>}{selected.evidence.target.model&&<span>{selected.evidence.target.model}</span>}<span>{t('P95 耗时')} {selected.summary?.p95_ms==null?'—':`${selected.summary.p95_ms} ms`}</span></div>
      {selected.execution_available!==true&&<p role="status">{t('这是旧版配置记录，请选择原数据版本新建可执行的评测。')}</p>}
      {selected.error&&<p className="alert" role="alert">{t(evaluationFailures[selected.error]||selected.error)}</p>}
      <div className="evaluation-tabs" role="tablist" aria-label={t('评测详情导航')}>{tabs.map(([value,label],index)=><button key={value} type="button" role="tab" id={`${tabId}-${value}-tab`} aria-selected={tab===value} aria-controls={`${tabId}-${value}-panel`} tabIndex={tab===value?0:-1} onClick={()=>setTab(value)} onKeyDown={event=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();
        const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;
        setTab(tabs[next][0]);event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
      }}>{t(label)}</button>)}</div>
      <div className={`evaluation-detail-tabpanel evaluation-tab-${tab}`} role="tabpanel" id={`${tabId}-${tab}-panel`} aria-labelledby={`${tabId}-${tab}-tab`}>
        {tab==='results'&&<EvaluationScores run={selected} language={language}/>}
        {tab==='details'&&<EvaluationSamples key={selected.id} run={selected} version={version} language={language}/>}
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
    {exporting&&selected&&<WorkspaceDialog title={t('导出评测结果')} busy={busy} language={language} onClose={()=>setExporting(false)}><form className="workspace-dialog-body" onSubmit={event=>{event.preventDefault();void act(async()=>{const response=await authenticatedFetch(`/api/evaluation-runs/${selected.id}/export?format=${format}`);if(!response.ok)throw new Error(t('导出失败'));downloadBlob(await response.blob(),`evaluation-${selected.id}.${format}`);setExporting(false)})}}><label className="field"><span>{t('文件格式')}</span><select aria-label={t('文件格式')} value={format} onChange={event=>setFormat(event.target.value)}>{['json','jsonl','csv'].map(value=><option key={value}>{value}</option>)}</select></label><p>{t('导出包含冻结参考资料和实际回答，请按空间数据权限保管。')}</p><button className="primary" disabled={busy}>{t('导出')}</button></form></WorkspaceDialog>}
    {confirmStart&&dataset&&<WorkspaceDialog className="evaluation-start-confirm" title={t('确认评测参数')} busy={busy} language={language} onClose={()=>{if(!busy)setConfirmStart(false)}}>
      <div className="workspace-dialog-body">
        <p className="workspace-muted">{t('以下参数来自当前数据集的评测配置，确认后将立即发起评测。')}</p>
        {dataset.evaluation_config ? <dl className="evaluation-confirm-parameters">
          <div><dt>{t('数据集')}</dt><dd>{dataset.name} · {dataset.item_count} {t('样本')}</dd></div>
          <div><dt>{t('评测类型')}</dt><dd>{t(evaluationTypeLabels[dataset.evaluation_config.evaluation_type])}</dd></div>
          <div><dt>{t('评分引擎')}</dt><dd>{dataset.evaluation_config.scoring_engine}</dd></div>
          <div><dt>{t('评测样本')}</dt><dd>{dataset.evaluation_config.sample_limit == null ? t('全部样本') : dataset.evaluation_config.sample_limit}</dd></div>
          <div><dt>{t('评分指标')}</dt><dd>{dataset.evaluation_config.metrics.map(metric=>metricName(metric)).join('、') || '—'}</dd></div>
          <div><dt>{t('被测对象')}</dt><dd>{dataset.evaluation_config.target.selection?.model || dataset.evaluation_config.target.selection?.id || '—'}</dd></div>
          {dataset.evaluation_config.target.retrieval_top_k != null && <div><dt>{t('最终 Top K')}</dt><dd>{dataset.evaluation_config.target.retrieval_top_k}</dd></div>}
          {dataset.evaluation_config.target.retrieval_candidate_top_k != null && <div><dt>{t('每库召回数量')}</dt><dd>{dataset.evaluation_config.target.retrieval_candidate_top_k}</dd></div>}
          {dataset.evaluation_config.target.retrieval_reranker && <div><dt>{t('重排模型')}</dt><dd>{dataset.evaluation_config.target.retrieval_reranker.model || dataset.evaluation_config.target.retrieval_reranker.id || '—'}</dd></div>}
        </dl> : <p role="alert">{t('请先在数据集设置的评测配置中完成配置，再发起评测。')}</p>}
      </div>
      <div className="workspace-dialog-footer"><button type="button" className="ghost" disabled={busy} onClick={()=>setConfirmStart(false)}>{t('取消')}</button><button type="button" className="primary" disabled={busy||!dataset.evaluation_config} onClick={()=>{setConfirmStart(false);void startConfiguredEvaluation()}}>{t(busy?'发起中…':'确认发起')}</button></div>
    </WorkspaceDialog>}
  </section>;
}
