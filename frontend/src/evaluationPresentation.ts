import type {CaseResult, Evaluation, EvaluationSummary, MetricResult} from './evaluationApi';

export const evaluationLabels:Record<string,string> = {
  prepared:'已准备', running:'运行中', completed:'已完成', partial_failed:'部分失败', failed:'失败', cancelled:'已取消',
  pending:'等待中', judge_error:'评分失败', target_error:'应用调用失败', missing_data:'缺少数据',
  passed:'通过', not_passed:'未通过', unscored:'未提供分数',
};
export const evaluationFailures:Record<string,string> = {
  invalid_request:'服务拒绝了请求，请检查目标服务与参数配置。',
  reranker_input_too_long:'重排输入超过模型长度上限，请使用支持更长输入的重排模型，或缩短知识库片段。',
  reranker_request_rejected:'重排服务拒绝了请求，请检查重排模型名称、接口和参数配置。',
  dify_retrieval_configuration_missing:'无法读取 Dify 知识库的检索配置，请检查 Dataset API 权限和知识库设置。',
  dify_retrieval_rejected:'Dify 拒绝了检索请求，请检查知识库的检索方式、重排模型和阈值配置。',
  authentication_failed:'身份验证失败，请检查目标服务的 API Key 和访问权限。',
  quota_exhausted:'服务额度不足，请检查目标服务账户余额或配额。',
  budget_exhausted:'评测请求或 Token 预算已用完，请调整预算后重试。',
  configuration_changed:'目标或评分模型配置已变更，请新建评测以使用当前配置。',
  service_unavailable:'目标或评分服务暂时不可用，请稍后重试。',
  target_call_failed:'目标服务调用失败，请稍后重试。', judge_call_failed:'评分服务调用失败，请稍后重试。',
};
export const hasScore = (result:MetricResult) => result.status==='completed' && typeof result.score==='number' && Number.isFinite(result.score);

export function scoringReasons(text:string):string[]|null {
  // SDKs often return nested JSON evidence. Show its explanations as prose,
  // keeping the complete source available in the sample detail disclosure.
  let value:unknown;
  try {value=JSON.parse(text)} catch {return null}
  const reasons:string[]=[];
  function visit(item:unknown,depth:number) {
    if(depth>12||reasons.length>=100)return;
    if(Array.isArray(item)){for(const entry of item)visit(entry,depth+1)}
    else if(item&&typeof item==='object'){
      for(const [key,entry] of Object.entries(item)) {
        if(key==='reason'&&typeof entry==='string'&&entry.trim())reasons.push(entry);
        else if(key!=='reason')visit(entry,depth+1);
      }
    }
  }
  visit(value,0);
  return reasons.length?reasons:null;
}

export function metricOverview(run:Evaluation) {
  return run.evidence.metrics.map(metric=>{
    const results = run.metric_results.filter(result=>result.metric_id===metric.id);
    const valid = results.filter(hasScore);
    const summary = run.summary?.metrics?.[metric.id];
    const mean = summary ? summary.mean : valid.length ? valid.reduce((sum,result)=>sum+result.score!,0)/valid.length : null;
    const scored = summary?.scored ?? valid.length;
    const passed = valid.filter(result=>result.passed===true).length;
    const passRate = valid.length && valid.every(result=>typeof result.passed==='boolean') ? passed/valid.length : null;
    const threshold = metric.threshold?.value ?? metric.threshold?.default;
    const unscored:Record<string,number> = {};
    const distribution = [0,0,0,0,0];
    for (const result of results) {
      if (hasScore(result)) distribution[Math.min(4,Math.max(0,Math.floor(result.score!*5)))]++;
      else {const status=result.status==='completed'?'unscored':result.status;unscored[status]=(unscored[status]||0)+1;}
    }
    const absent = Math.max(0,run.case_count-results.length);
    if (absent) unscored.pending=(unscored.pending||0)+absent;
    return {metric,mean,scored,passed,passRate,threshold,unscored,distribution};
  });
}

export function caseVerdict(item:CaseResult, results:MetricResult[], metricCount:number) {
  if (item.status==='target_error' || item.status==='cancelled') return item.status;
  if (results.some(result=>result.status==='judge_error')) return 'judge_error';
  if (results.some(result=>result.status==='missing_data')) return 'missing_data';
  if (item.status==='pending' || results.length<metricCount || results.some(result=>result.status==='pending')) return 'pending';
  if (results.some(result=>result.status==='cancelled')) return 'cancelled';
  if (results.some(result=>hasScore(result) && result.passed===false)) return 'not_passed';
  if (metricCount && results.length===metricCount && results.every(result=>hasScore(result) && result.passed===true)) return 'passed';
  return item.status==='completed' && metricCount ? 'unscored' : item.status;
}

export function recallLabel(k?:number) {
  return typeof k==='number' && Number.isInteger(k) && k>0 ? `Recall@${k}` : 'Recall@K';
}

export function resultColumns(runs:EvaluationSummary[]) {
  const columns:Array<{id:string;k?:number;key:string}>=[];
  const seen=new Set<string>();
  for(const run of runs) for(const [id,summary] of Object.entries(run.summary?.metrics||{})) {
    const k=id==='rules.recall_at_k'?summary.k:undefined;
    const key=id==='rules.recall_at_k'?`${id}:${k??'unknown'}`:id;
    if(!seen.has(key)){seen.add(key);columns.push({id,k,key})}
  }
  return columns;
}
