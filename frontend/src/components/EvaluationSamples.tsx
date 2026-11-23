import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,Check,Search,X} from 'lucide-react';
import type {Evaluation,MetricResult,Version} from '../evaluationApi';
import {tr,type Language} from '../i18n';
import {caseVerdict,evaluationFailures,evaluationLabels,hasScore,scoringReasons} from '../evaluationPresentation';
import {evaluationType} from '../evaluationTypes';
import {metricName} from './EvaluationDialog';
import {EvaluationStatus} from './EvaluationStatus';
import {WorkspaceDialog} from './WorkspaceDialog';
import {Pagination,usePagination} from './Pagination';
import {pageItems} from '../pagination';

function Score({result,language}:{result:MetricResult|undefined;language:Language}) {
  const t=(text:string)=>tr(language,text);
  if(!result||!hasScore(result))return <span className="evaluation-cell-unscored">—<small>{t(evaluationLabels[result?.status==='completed'?'unscored':result?.status||'pending']||result?.status||'等待中')}</small></span>;
  return <span className="evaluation-cell-score" data-tone={result.passed===true?'success':result.passed===false?'failure':'neutral'}>
    {result.passed===true?<Check aria-hidden="true"/>:result.passed===false?<X aria-hidden="true"/>:null}
    <span>{result.score!.toFixed(3)}<small>{result.passed==null?t('已评分'):t(result.passed?'通过':'未通过')}</small></span>
  </span>;
}

function evidenceText(value:unknown, empty:string) {
  if (value == null || value === '') return empty;
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) {
    const items = value.map(item => typeof item === 'string' ? item : JSON.stringify(item, null, 2));
    return items.length ? items.join('\n\n') : empty;
  }
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}

function evidenceItems(value:unknown) {
  if (Array.isArray(value)) return value.map(item => evidenceText(item, '')).filter(Boolean);
  if (value == null || value === '') return [];
  return [evidenceText(value, '')];
}

export function EvaluationSamples({run,version,language}:{run:Evaluation;version:Version|null;language:Language}) {
  const t=(text:string)=>tr(language,text),retrieval=evaluationType(run.evidence)==='retrieval';
  const [query,setQuery]=useState(''),[filter,setFilter]=useState('all'),[sort,setSort]=useState<string|null>(null),[caseId,setCaseId]=useState<string|null>(null);
  const content=useRef<HTMLDivElement>(null),headerScroll=useRef<HTMLDivElement>(null);
  const samples=useMemo(()=>new Map(version?.samples.map(sample=>[sample.id,sample])||[]),[version]);
  const results=useMemo(()=>{
    const grouped=new Map<string,Map<string,MetricResult>>();
    for(const result of run.metric_results){if(!grouped.has(result.case_id))grouped.set(result.case_id,new Map());grouped.get(result.case_id)!.set(result.metric_id,result)}
    return grouped;
  },[run.metric_results]);
  const visible=useMemo(()=>{
    const found=run.cases.filter(item=>{
      const scores=[...(results.get(item.id)?.values()||[])],sample=samples.get(item.sample_id);
      const matches=!query.trim()||`${sample?.input||''} ${item.sample_id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
      return matches&&(filter==='all'||filter==='not_passed'&&scores.some(result=>hasScore(result)&&result.passed===false)||filter==='failed'&&(item.status==='target_error'||scores.some(result=>result.status==='judge_error'))||filter==='missing_data'&&scores.some(result=>result.status==='missing_data'));
    });
    if(sort){
      const [direction,metricId]=sort.split('|');
      found.sort((a,b)=>{const first=results.get(a.id)?.get(metricId),second=results.get(b.id)?.get(metricId);const x=first&&hasScore(first)?first.score:null,y=second&&hasScore(second)?second.score:null;
        if(x==null)return y==null?0:1;if(y==null)return -1;return direction==='asc'?x-y:y-x;
      });
    }
    return found;
  },[run.cases,results,samples,query,filter,sort]);
  const pagination=usePagination(visible.length,JSON.stringify([query,filter,sort]),10);
  const pageVisible=useMemo(()=>pageItems(visible,pagination.page,pagination.pageSize),[visible,pagination.page,pagination.pageSize]);
  const chosen=run.cases.find(item=>item.id===caseId),sample=chosen?samples.get(chosen.sample_id):undefined;
  const position=visible.findIndex(item=>item.id===caseId);
  const evidenceFields=chosen ? [
    {label:t('参考答案'), value:sample?.expected_output},
    ...(!retrieval ? [{label:t('实际回答'), value:chosen.observation?.actual_output}] : []),
    {label:t('参考资料'), value:evidenceText(sample?.context,t('未提供')) + (() => {
      const relevantIds=evidenceItems(sample?.annotations?.relevant_chunk_ids);
      const graded=sample?.annotations?.graded_relevance;
      const grades=graded && typeof graded === 'object' && !Array.isArray(graded) ? graded as Record<string,unknown> : {};
      if (!relevantIds.length && !Object.keys(grades).length) return '';
      const labels=relevantIds.length ? relevantIds : Object.keys(grades);
      return `\n\n${t('相关片段标注')}\n${labels.map(id=>`${id}${grades[id] == null ? '' : `（${t('权重')}：${grades[id]}）`}`).join('\n')}`;
    })()},
    {label:t('实际召回'), value:(() => {
      const contexts=evidenceItems(chosen.observation?.retrieval_context);
      const ids=evidenceItems(chosen.observation?.retrieved_chunk_ids);
      if (!contexts.length) return ids.length ? ids.map(id=>`${t('片段 ID')}：${id}`).join('\n') : undefined;
      return contexts.map((context,index)=>`${ids[index] ? `${t('片段 ID')}：${ids[index]}\n` : ''}${context}`).join('\n\n');
    })()},
  ] : [];
  const columns=run.evidence.metrics.length+3;
  const tableColumns=()=> <colgroup><col style={{width:'40%'}}/>{Array.from({length:columns-1},(_,index)=><col key={index} style={{width:`${60/(columns-1)}%`}}/>)}</colgroup>;
  const toggleSort=(metricId:string)=>setSort(current=>{const [direction,currentId]=current?.split('|')||[]; return currentId===metricId&&direction==='asc'?`desc|${metricId}`:`asc|${metricId}`;});
  const sortHeader=(metric:{id:string})=>{const active=sort?.split('|')[1]===metric.id; const direction=sort?.split('|')[0]; return <button type="button" className="evaluation-sort-button" onClick={()=>toggleSort(metric.id)} aria-label={`${t(metricName(metric))}${active?`，${direction==='asc'?t('升序'):t('降序')}`:''}`}>{t(metricName(metric))}{active&&<span aria-hidden="true"> {direction==='asc'?'↑':'↓'}</span>}</button>};
  const tableHeader=()=> <tr><th scope="col">{t('问题')}</th><th scope="col">{t('状态')}</th>{run.evidence.metrics.map(metric=><th scope="col" key={metric.id}>{sortHeader(metric)}</th>)}<th scope="col">{t('耗时')}</th></tr>;
  const syncHeaderScroll=(event:React.UIEvent<HTMLDivElement>)=>{if(headerScroll.current)headerScroll.current.scrollLeft=event.currentTarget.scrollLeft};
  useEffect(()=>{content.current?.scrollTo(0,0)},[caseId]);
  return <section className="evaluation-samples" aria-label={t('样本明细')}>
    <div className="evaluation-sample-toolbar">
      <label className="filter-control"><select aria-label={t('结果筛选')} value={filter} onChange={event=>setFilter(event.target.value)}>{[['all','全部结果'],['not_passed','未通过'],['failed','执行失败'],['missing_data','不可计算']].map(([value,label])=><option key={value} value={value}>{t(label)}</option>)}</select></label>
      <label className="evaluation-search"><Search aria-hidden="true"/><input aria-label={t('搜索样本问题')} placeholder={t('搜索样本问题')} value={query} onChange={event=>setQuery(event.target.value)}/></label>
    </div>
    <div className="evaluation-sample-matrix" aria-label={t('样本指标得分表')}>
      <div className="evaluation-sample-header-scroll" ref={headerScroll} aria-hidden="true"><table className="data-table">{tableColumns()}<thead>{tableHeader()}</thead></table></div>
      <div className="evaluation-sample-body-scroll" tabIndex={0} onScroll={syncHeaderScroll}><table className="data-table">{tableColumns()}<thead className="evaluation-sample-accessible-head">{tableHeader()}</thead><tbody>{pageVisible.map(item=>{
        const scores=results.get(item.id),question=samples.get(item.sample_id)?.input||item.sample_id;
        return <tr key={item.id} data-case-id={item.id}><td><button className="evaluation-question" onClick={()=>setCaseId(item.id)} title={question}>{question}</button></td><td><EvaluationStatus status={caseVerdict(item,[...(scores?.values()||[])],run.evidence.metrics.length)} language={language}/></td>{run.evidence.metrics.map(metric=><td key={metric.id}><Score result={scores?.get(metric.id)} language={language}/></td>)}<td>{item.observation?.duration_ms==null?'—':`${item.observation.duration_ms.toLocaleString(language)} ms`}</td></tr>;
      })}</tbody></table></div>
    </div>
    {visible.length>0&&<Pagination {...pagination} total={visible.length} language={language} label={t('样本')} pageSizes={[10,20,50]} />}
    {!visible.length&&<p className="dify-empty">{t('没有匹配的样本')}</p>}
    {chosen&&<WorkspaceDialog title={t('样本详情')} busy={false} language={language} onClose={()=>setCaseId(null)} className="evaluation-sample-drawer">
      <div className="evaluation-drawer-nav"><EvaluationStatus status={caseVerdict(chosen,[...(results.get(chosen.id)?.values()||[])],run.evidence.metrics.length)} language={language}/><div><span>{position>=0?position+1:'—'} / {visible.length}</span><button className="ghost" disabled={position<=0} onClick={()=>setCaseId(visible[position-1].id)}><ArrowLeft/>{t('上一条')}</button><button className="ghost" disabled={position<0||position>=visible.length-1} onClick={()=>setCaseId(visible[position+1].id)}>{t('下一条')}<ArrowRight/></button></div></div>
      <div className="evaluation-drawer-content" ref={content}>
        <section className="evaluation-drawer-question"><h3>{t('问题')}</h3><p>{sample?.input||chosen.sample_id}</p></section>
        <section className="evaluation-sample-verdicts"><h3>{t('指标得分与理由')}</h3>{run.evidence.metrics.map(metric=>{
          const result=results.get(chosen.id)?.get(metric.id),threshold=metric.threshold?.value??metric.threshold?.default;
          const reasons=result?.reason?scoringReasons(result.reason):null;
          return <article key={metric.id}><header><div><h4>{t(metricName(metric))}</h4><small>{t('通过阈值')} {threshold==null?'—':`≥ ${threshold.toFixed(3)}`}</small></div><Score result={result} language={language}/></header>
            {result?.usage?.cache_hit&&<small>{t('已复用评分')}</small>}
            {result?.reason?(reasons?<><ul className="evaluation-reasons">{reasons.map((reason,index)=><li key={index}>{reason}</li>)}</ul><details><summary>{t('原始评分信息')}</summary><pre>{result.reason}</pre></details></>:<pre className="evaluation-reason">{result.reason}</pre>):result?.error?<p role="alert">{t(evaluationFailures[result.error]||result.error)}</p>:<p className="evaluation-guidance">{result?.dependency?.missing_fields?.length?`${t('缺少数据')}: ${result.dependency.missing_fields.join(', ')}`:result?.dependency?.awaiting_fields?.length?`${t('等待中')}: ${result.dependency.awaiting_fields.join(', ')}`:t(result?.status==='completed'?'未提供评分理由':'等待评分结果')}</p>}
          </article>;
        })}{!run.evidence.metrics.length&&<p>{t('本次评测未配置评分指标。')}</p>}</section>
        {chosen.error&&<p className="alert" role="alert">{t(evaluationFailures[chosen.error]||chosen.error)}</p>}
        <div className="evaluation-evidence-grid">{evidenceFields.map(({label,value})=><section className="evaluation-evidence-card" key={label}><h3>{label}</h3><pre>{evidenceText(value,t('未提供'))}</pre></section>)}</div>
        {sample?.scenario!=null&&<details><summary>{t('场景配置')}</summary><pre>{JSON.stringify(sample.scenario,null,2)}</pre></details>}
        {chosen.observation?.turns!=null&&<details><summary>{t('实际对话轮次')}</summary><pre>{JSON.stringify(chosen.observation.turns,null,2)}</pre></details>}
        {chosen.observation?.tools_called!=null&&<details><summary>{t('实际工具轨迹')}</summary><pre>{JSON.stringify(chosen.observation.tools_called,null,2)}</pre></details>}
        {chosen.observation?.metadata!=null&&<details><summary>{t('召回来源与运行信息')}</summary><pre>{JSON.stringify(chosen.observation.metadata,null,2)}</pre></details>}
      </div>
    </WorkspaceDialog>}
  </section>;
}
