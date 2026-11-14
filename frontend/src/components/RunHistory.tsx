import {useCallback, useEffect, useState} from 'react';
import {ArrowLeft, History, RefreshCw, Sparkles, Square, Trash2} from 'lucide-react';
import {api, type ListFilters} from '../api';
import {useBulkSelection, type BulkResult} from '../bulkSelection';
import {useServerPagination} from '../useServerPagination';
import {tr, type Language} from '../i18n';
import type {Run, RunPlanGroup, TestPlan} from '../types';
import {PageHeader} from './PageHeader';
import {EmptyState} from './EmptyState';
import {ListToolbar} from './ListToolbar';
import {Pagination} from './Pagination';
import {BulkSelectionBar, SelectionCheckbox} from './BulkSelection';
import {AnalysisDashboard} from './AnalysisDashboard';

const terminal = new Set(['completed','failed','cancelled']);
const statusNames:Record<string,string> = {queued:'等待中',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消'};
const types = ['llm','embedding','reranker','audio','dify-retrieve','dify-chat'];
function Status({run,language}:{run:Run;language:Language}) {
  return <span className={`status status-${run.status}`}><i/>{tr(language,statusNames[run.status] || run.status)}</span>;
}
function time(run:Run, language:Language) {
  return new Date(run.created_at).toLocaleString(language);
}
function Feedback({page,language}:{page:{items:unknown[];loading:boolean;error:string;retry:()=>void};language:Language}) {
  const t=(value:string)=>tr(language,value);
  return <>{page.loading&&!page.items.length&&<p className="pagination-feedback" role="status">{t('加载中…')}</p>}{page.error&&<div className="pagination-feedback error-text" role="alert">{page.error}<button type="button" className="ghost" onClick={page.retry}>{t('重试')}</button></div>}</>;
}

function PlanRecords({group,language,canManage,revision,selectedId,onSelect,onReport,onChanged,onDeleted,onCount}:{
  group:RunPlanGroup;language:Language;canManage:boolean;revision:number;selectedId:string|null;
  onSelect:(run:Run)=>void;onReport:(run:Run)=>void;onChanged:()=>void;onDeleted:(ids:string[])=>void;onCount:(count:number)=>void;
}) {
  const t=(value:string)=>tr(language,value);
  const [notice,setNotice]=useState('');
  const [deleting,setDeleting]=useState(false);
  const fetchPage=useCallback(async(page:number,size:number,signal:AbortSignal,filters:ListFilters)=>{
    if(group.plan_id)return api.runsPage(page,size,signal,{...filters,plan_id:group.plan_id});
    try {
      const run=await api.run(group.latest_run.id,signal);
      return {items:[run],page:1,page_size:size,total:1};
    } catch(reason) {
      if(reason instanceof Error&&reason.message==='Run not found')return {items:[],page:1,page_size:size,total:0};
      throw reason;
    }
  },[group.id,group.plan_id,group.latest_run.id]);
  const page=useServerPagination(fetchPage,true,revision);
  const selection=useBulkSelection(group.id);
  const ids=page.items.map(run=>run.id),disabled=page.loading||!!page.error||selection.busy||deleting;
  useEffect(()=>{if(!page.loading&&!page.error)onCount(page.total)},[page.total,page.loading,page.error]);
  function changed(result:BulkResult, action:'delete'|'cancel') {if(action==='delete')onDeleted(result.succeeded);onChanged();}
  function bulk(action:'delete'|'cancel') {
    if(!window.confirm(`${t(action==='cancel'?'批量取消运行':'批量删除')} ${selection.ids.size} ${t('条记录')}？ ${t(action==='cancel'?'仅取消排队中或运行中的任务。':'仅删除已结束的运行及其报告，此操作无法撤销。')}`))return;
    void selection.execute(ids=>api.bulkRuns(action,ids),result=>changed(result,action));
  }
  async function remove(run:Run) {
    if(!window.confirm(`${t('删除运行记录')} “${run.name}” · ${time(run,language)}? ${t('此操作无法撤销。')}`))return;
    setDeleting(true);setNotice('');
    try{await api.deleteRun(run.id);onDeleted([run.id]);onChanged();}
    catch(reason){setNotice(reason instanceof Error?reason.message:String(reason));}
    finally{setDeleting(false);}
  }
  return <section className="panel plan-run-records" aria-label={t('全部运行记录')}>
    <BulkSelectionBar selection={selection} language={language}>
      <button type="button" className="ghost" disabled={disabled} onClick={()=>bulk('cancel')}><Square/>{t('批量取消运行')}</button>
      <button type="button" className="ghost danger" disabled={!canManage||disabled} onClick={()=>bulk('delete')}><Trash2/>{t('批量删除')}</button>
    </BulkSelectionBar>
    {notice&&<p className="alert" role="alert">{notice}</p>}
    <div className="table-scroll"><table><thead><tr>
      <th className="selection-cell"><SelectionCheckbox label={t('全选当前页')} checked={!!ids.length&&ids.every(id=>selection.ids.has(id))} mixed={ids.some(id=>selection.ids.has(id))&&!ids.every(id=>selection.ids.has(id))} disabled={disabled||!ids.length} onChange={checked=>selection.toggle(ids,checked)}/></th>
      <th>{t('运行时间')}</th><th>{t('状态')}</th><th>{t('进度')}</th><th>{t('操作')}</th>
    </tr></thead><tbody>{page.items.map(run=><tr key={run.id} className={selectedId===run.id?'selected':''}>
      <td className="selection-cell"><SelectionCheckbox label={`${t('选择记录')} ${run.id}`} checked={selection.ids.has(run.id)} disabled={disabled} onChange={checked=>selection.toggle([run.id],checked)}/></td>
      <td><button type="button" className="text-button run-history-link" onClick={()=>onSelect(run)}><time dateTime={run.created_at}>{time(run,language)}</time></button></td>
      <td><Status run={run} language={language}/></td><td>{run.completed_scenarios} / {run.total_scenarios}</td>
      <td><div className="row-actions"><button type="button" className="ghost" disabled={!terminal.has(run.status)} onClick={()=>onReport(run)}><Sparkles/>{t('AI 报告')}</button><button type="button" className="icon-button danger" aria-label={t('删除运行记录')} disabled={!canManage||disabled||!terminal.has(run.status)} onClick={()=>void remove(run)}><Trash2/></button></div></td>
    </tr>)}</tbody></table></div>
    {!page.loading&&!page.error&&!page.total&&<EmptyState icon={History} title={t('暂无运行记录')}/>}
    <Feedback page={page} language={language}/><Pagination {...page} total={page.total} language={language} label={t('全部运行记录')} disabled={disabled}/>
  </section>;
}

export function RunHistory({plans,runs,language,dark,canManage,revision,onSelectRun,onReport,onChanged}:{
  plans:TestPlan[];runs:Run[];language:Language;dark:boolean;canManage:boolean;revision:number;
  onSelectRun:(run:Run)=>void;onReport:(run:Run)=>void;onChanged:()=>void;
}) {
  const t=(value:string)=>tr(language,value);
  const [filters,setFilters]=useState<ListFilters>({});
  const [group,setGroup]=useState<RunPlanGroup|null>(null);
  const [selected,setSelected]=useState<Run|null>(null);
  const [tab,setTab]=useState<'result'|'records'>('result');
  const [localRevision,setLocalRevision]=useState(0);
  const page=useServerPagination(api.runPlanGroups,!group,revision+localRevision,filters);
  const fresh=selected&&runs.find(run=>run.id===selected.id&&run.updated_at>selected.updated_at);
  const current=fresh||selected;
  function choose(run:Run) {setSelected(run);setTab('result');onSelectRun(run);window.scrollTo(0,0);}
  function open(value:RunPlanGroup) {setGroup(value);choose(value.latest_run);}
  function changed() {setLocalRevision(value=>value+1);onChanged();}
  if(group)return <div className="run-history-detail">
    <PageHeader icon={History} title={group.name} breadcrumb={<button type="button" className="ghost" onClick={()=>{setGroup(null);setSelected(null)}}><ArrowLeft/>{t('所有测试计划')}</button>} actions={<button type="button" className="ghost" onClick={changed}><RefreshCw/>{t('刷新')}</button>}/>
    <div className="run-history-tabs" role="group" aria-label={t('计划运行记录')}>
      <button type="button" aria-pressed={tab==='result'} disabled={!current} onClick={()=>setTab('result')}>{t('运行结果')}</button>
      <button type="button" aria-pressed={tab==='records'} onClick={()=>setTab('records')}>{t('全部运行记录')} <span>{group.run_count}</span></button>
    </div>
    {tab==='records'?<PlanRecords key={group.id} group={group} language={language} canManage={canManage} revision={revision+localRevision} selectedId={current?.id||null} onSelect={choose} onReport={onReport} onChanged={changed} onDeleted={ids=>{if(current&&ids.includes(current.id))setSelected(null)}} onCount={count=>setGroup(value=>value&&value.run_count!==count?{...value,run_count:count}:value)}/>:current&&<>
      <div className="run-history-current"><div><Status run={current} language={language}/><time dateTime={current.created_at}>{time(current,language)}</time><span>{current.completed_scenarios} / {current.total_scenarios} {t('场景')}</span></div><button type="button" className="ghost" disabled={!terminal.has(current.status)} onClick={()=>onReport(current)}><Sparkles/>{t('AI 报告')}</button></div>
      {current.error&&<p className="alert" role="alert">{current.error}</p>}
      <AnalysisDashboard key={`${group.id}:${localRevision}:${revision}`} embedded fixedPlanId={group.plan_id||undefined} singleRun={group.plan_id?undefined:current} plans={plans} runs={runs} language={language} dark={dark} initialPlanId={group.plan_id||current.id} initialRunId={current.id} onSelectRun={choose}/>
    </>}
  </div>;
  return <section className="panel history run-plan-history">
    <PageHeader icon={History} title={t('运行记录')} count={page.total} description={t('按测试计划查看运行记录')}/>
    <ListToolbar language={language} search={filters.q||''} onSearch={q=>setFilters(value=>({...value,q}))} searchLabel={t('搜索测试计划')} onReset={()=>setFilters({})} filters={[
      {label:t('最近状态'),value:filters.status||'',onChange:status=>setFilters(value=>({...value,status})),options:Object.entries(statusNames).map(([value,label])=>({value,label:t(label)}))},
      {label:t('测试类型'),value:filters.benchmark||'',onChange:benchmark=>setFilters(value=>({...value,benchmark})),options:types.map(value=>({value,label:value}))},
    ]}/>
    <div className="table-scroll"><table><thead><tr><th>{t('测试计划')}</th><th>{t('类型')}</th><th>{t('运行次数')}</th><th>{t('最近状态')}</th><th>{t('最近运行')}</th><th>{t('操作')}</th></tr></thead><tbody>{page.items.map(value=><tr key={value.id}>
      <td><button type="button" className="text-button run-history-link" onClick={()=>open(value)}>{value.name}</button></td><td>{value.latest_run.benchmark}</td><td>{value.run_count}</td><td><Status run={value.latest_run} language={language}/></td><td><time dateTime={value.latest_run.created_at}>{time(value.latest_run,language)}</time></td><td><button type="button" className="ghost" onClick={()=>{open(value);setTab('records')}}>{t('全部运行记录')}</button></td>
    </tr>)}</tbody></table></div>
    {!page.loading&&!page.error&&!page.total&&<EmptyState icon={History} title={t(filters.q||filters.status||filters.benchmark?'没有匹配的记录，请调整搜索或筛选条件。':'暂无运行记录')}/>}
    <Feedback page={page} language={language}/><Pagination {...page} total={page.total} language={language} label={t('测试计划')} disabled={page.loading}/>
  </section>;
}
