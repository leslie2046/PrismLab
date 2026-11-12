import {useEffect, useRef, useState} from 'react';
import {Play, X} from 'lucide-react';
import type {Catalog, Dataset, DatasetSettings, LlmSelection} from '../types';
import {tr, type Language} from '../i18n';
import {createClientId} from '../clientId';
import {datasetApi, llmChoices, rerankerChoices, selectionValue} from '../datasetApi';
import {evaluationApi, type Evaluation, type EvaluationCreate, type Metric} from '../evaluationApi';
import {WorkspaceDialog} from './WorkspaceDialog';
import {SearchableSelect} from './SearchableSelect';
import {SearchableMultiSelect} from './SearchableMultiSelect';
import {InfoTip} from './InfoTip';
import {evaluationTypeLabels, type EvaluationType} from '../evaluationTypes';

export const metricNames:Record<string,string>={'deepeval.answer_relevancy':'回答相关性','deepeval.faithfulness':'回答忠实度','deepeval.reference_correctness':'参考答案正确性','deepeval.context_precision':'上下文精确率（有参考）','deepeval.context_recall':'上下文召回率（有参考）','ragas.faithfulness':'回答忠实度','ragas.context_recall':'上下文召回率（有参考）','ragas.context_precision':'上下文精确率（有参考）','ragas.context_precision_without_reference':'上下文精确率（无参考）','ragas.answer_accuracy':'事实正确性','rules.recall_at_k':'Recall@K','rules.mrr':'MRR','rules.ndcg':'nDCG','deepeval.custom':'自定义业务评分','rules.required_text':'必需短语','rules.json_schema':'JSON Schema'};
export const metricHelp:Record<string,string>={
  'rules.recall_at_k':'前 K 个检索结果中命中的相关片段数 ÷ 标注的相关片段总数。',
  'rules.mrr':'第一个相关片段出现位置的倒数。相关片段越早出现，分数越高；前 K 个结果没有命中时为 0。',
  'rules.ndcg':'根据相关性等级衡量排序质量。越相关的片段排在越前面，nDCG 越高。',
};

const fieldNames:Record<string,string> = {
  input:'问题', actual_output:'实际回答', expected_output:'参考答案', retrieval_context:'实际召回',
  relevant_chunk_ids:'相关片段标注', graded_relevance:'分级相关性标注', retrieved_chunk_ids:'实际片段 ID',
};
const errorText = (error:unknown) => error instanceof Error ? error.message : String(error);

export function EvaluationDialog({catalog, language, onClose, onCreated, fixedDataset}:{
  fixedDataset?:Dataset;
  catalog:Catalog|null; language:Language; onClose:()=>void; onCreated:(run:Evaluation)=>void;
}) {
  const t = (text:string) => tr(language, text);
  const key = useRef(''), lastPayload = useRef(''), advanced = useRef<HTMLDetailsElement>(null);
  if (!key.current) key.current = createClientId();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [dataset, setDataset] = useState(fixedDataset?.id || '');
  const [name, setName] = useState(''), [benchmark, setBenchmark] = useState(fixedDataset?.evaluation_purpose === 'retrieval' ? 'dify-retrieve' : 'llm');
  const [target, setTarget] = useState(''), [judge, setJudge] = useState('');
  const [engine, setEngine] = useState('deepeval'), [template, setTemplate] = useState(fixedDataset?.evaluation_purpose === 'retrieval' ? 'retrieval' : 'reference_qa');
  const [evaluationType, setEvaluationType] = useState<EvaluationType>(fixedDataset?.evaluation_purpose === 'retrieval' ? 'retrieval' : 'rag');
  const [topK, setTopK] = useState(5);
  const [knowledgeTargets, setKnowledgeTargets] = useState<string[]>([]);
  const [reranker, setReranker] = useState('');
  const [candidateK, setCandidateK] = useState(20);
  const [scoreThreshold, setScoreThreshold] = useState(0);
  const initializedTargets = useRef('');
  // null follows the recommended plan; an explicit list preserves the user's custom selection.
  const [chosen, setChosen] = useState<string[]|null>(null);
  const [thresholds, setThresholds] = useState<Record<string,number>>({});
  const [scope, setScope] = useState('5'), [customLimit, setCustomLimit] = useState('50');
  const [prompt, setPrompt] = useState(''), [criteria, setCriteria] = useState('');
  const [examples, setExamples] = useState(''), [phrases, setPhrases] = useState('');
  const [schema, setSchema] = useState('{"type":"object"}');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [loading, setLoading] = useState(true), [loaded, setLoaded] = useState(false), [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    Promise.all([(async()=>{
      if(fixedDataset)return {items:[fixedDataset],total:1};
      const first=await datasetApi.list(1,100,'');
      for(let page=2;page<=Math.ceil(first.total/100);page++) first.items.push(...(await datasetApi.list(page,100,'')).items);
      return first;
    })(), evaluationApi.capabilities(), datasetApi.settings()])
      .then(([data, capabilities, defaults]: [ {items: Dataset[]}, {metrics: Metric[]}, DatasetSettings]) => {
        if (!active) return;
        setDatasets(data.items); setMetrics(capabilities.metrics);
        const compatible=data.items.filter(item=>!item.evaluation_purpose || ['both',evaluationType].includes(item.evaluation_purpose));
        setDataset(fixedDataset?.id || compatible.find(item => item.item_count > 0)?.id || compatible[0]?.id || '');
        setJudge(selectionValue(defaults.default_judge_llm)); setEngine(defaults.scoring_engine);
        setReranker(selectionValue(defaults.default_reranker || null));
        setLoaded(true);
      }).catch(error => { if (active) setError(errorText(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reload]);
  const compatibleDatasets = datasets.filter(item => !item.evaluation_purpose || item.evaluation_purpose === 'both' || item.evaluation_purpose === evaluationType);
  const llms = llmChoices(catalog, language);
  const rerankers = rerankerChoices(catalog, language);
  const targets = benchmark === 'llm' ? llms
    : (catalog?.benchmarks.find(b => b.id === benchmark)?.providers || []).flatMap(p => p.models.flatMap(m =>
      (m.credentials?.length ? m.credentials : [null]).map(c => ({
        value:JSON.stringify({id:p.id,model:m.alias||m.name,credential_id:c?.id||null}),
        label:[p.label, m.alias || m.name, c?.name].filter(value => value && value !== 'null').join(' · '),
      }))));
  const available = metrics.filter(m => m.executor_available && m.sample_kinds.includes('single_turn')
    && (m.engine === engine || m.engine === 'rules')
    && (template !== 'reference_free' || !m.required_fields.includes('expected_output'))
    && (evaluationType !== 'retrieval' || m.evaluation_types?.includes('retrieval')));
  let recommended:string[] = [];
  if (evaluationType === 'retrieval') {
    recommended = engine === 'rules' ? ['rules.recall_at_k','rules.mrr','rules.ndcg'] : [`${engine}.context_precision`,`${engine}.context_recall`];
  } else {
    if (engine === 'deepeval') recommended = ['deepeval.answer_relevancy', ...(template === 'reference_qa' ? ['deepeval.reference_correctness'] : [])];
    if (engine === 'ragas') recommended = template === 'reference_qa' ? ['ragas.answer_accuracy']
      : benchmark === 'llm' ? [] : ['ragas.faithfulness'];
  }
  const effective = (chosen ?? recommended).filter(id => available.some(m => m.id === id));
  const needsJudge = effective.some(id => metrics.find(m => m.id === id)?.engine !== 'rules');
  const limit = scope === 'all' ? null : Number(scope === 'custom' ? customLimit : scope);
  const multiRoute = evaluationType === 'retrieval' && knowledgeTargets.length > 1;
  const validReranker = !multiRoute || (!!reranker && rerankers.some(option => option.value === reranker));
  const validTarget = evaluationType === 'retrieval'
    ? knowledgeTargets.length > 0 && knowledgeTargets.every(value => targets.some(option => option.value === value)) && validReranker
    : targets.some(option => option.value === target);
  const validJudge = !needsJudge || llms.some(option => option.value === judge);
  const canSubmit = loaded && compatibleDatasets.some(item=>item.id===dataset) && validTarget && validJudge && effective.length > 0;
  const referenceRequired = effective.some(id => metrics.find(m => m.id === id)?.required_fields.includes('expected_output'));
  const retrievalRequired = effective.some(id => metrics.find(m => m.id === id)?.required_fields.includes('retrieval_context'));
  const buttonText = busy ? t('提交中…') : limit === null ? t('开始全部评测') : t('开始试跑');

  useEffect(() => {
    if (evaluationType !== 'retrieval' || benchmark !== 'dify-retrieve') return;
    if (!targets.length || initializedTargets.current === dataset) return;
    const current = datasets.find(item => item.id === dataset);
    if (!current) return;
    const configIds = new Set([...(current.dify_config_ids || []),
      ...(current.data_sources || []).filter(source => source.kind === 'dify').map(source => source.config_id), current.dify_config_id].filter(Boolean));
    const preferred = targets.filter(option => configIds.has((JSON.parse(option.value) as LlmSelection).id));
    setKnowledgeTargets(preferred.slice(0,20).map(option => option.value));
    initializedTargets.current = dataset;
  }, [dataset, evaluationType, benchmark, datasets, catalog]);

  function changeType(value:EvaluationType) {
    if(fixedDataset?.evaluation_purpose && !['both',value].includes(fixedDataset.evaluation_purpose))return;
    setEvaluationType(value);setChosen(null);setTarget('');setKnowledgeTargets([]);initializedTargets.current='';
    setBenchmark(value === 'retrieval' ? 'dify-retrieve' : 'llm');setTemplate(value === 'retrieval' ? 'retrieval' : 'reference_qa');
    const compatible=datasets.filter(item=>!item.evaluation_purpose || item.evaluation_purpose==='both' || item.evaluation_purpose===value);
    if(!compatible.some(item=>item.id===dataset))setDataset(compatible.find(item=>item.item_count>0)?.id || compatible[0]?.id || '');
  }
  return <WorkspaceDialog className="evaluation-dialog" title={t('新建评测')} busy={busy} language={language} onClose={onClose}>
    <form className="evaluation-form" onSubmit={async event => {
      event.preventDefault();
      if (!canSubmit || busy) return;
      setBusy(true); setError('');
      try {
        const payload:EvaluationCreate = {
          evaluation_type:evaluationType, dataset_id:dataset,
          sample_kinds:['single_turn'],
          name:name.trim() || `${datasets.find(item => item.id === dataset)?.name || t('数据集')} · ${t(evaluationTypeLabels[evaluationType])}`.slice(0,120),
          idempotency_key:key.current, template, sample_limit:limit,
          target:{benchmark,selection:JSON.parse(evaluationType==='retrieval'?knowledgeTargets[0]:target) as LlmSelection,prompt:evaluationType==='retrieval'?'':prompt,max_tokens:1024,retrieval_top_k:topK,
            ...(multiRoute?{selections:knowledgeTargets.map(value=>JSON.parse(value) as LlmSelection),
              retrieval_reranker:reranker?JSON.parse(reranker):null,retrieval_candidate_top_k:candidateK,retrieval_score_threshold:scoreThreshold}:{})},
          scoring_engine:engine, judge:needsJudge && judge ? JSON.parse(judge) : null,
          metrics:effective.map(id => ({id, ...(['rules.recall_at_k','rules.mrr','rules.ndcg'].includes(id)?{k:topK}:{}), threshold:thresholds[id] ?? metrics.find(m => m.id === id)?.threshold.default ?? .5,
            ...(id === 'deepeval.custom' ? {criteria,examples} : {}),
            ...(id === 'rules.required_text' ? {required_text:phrases.split('\n').map(s => s.trim()).filter(Boolean)} : {}),
            ...(id === 'rules.json_schema' ? {json_schema:JSON.parse(schema)} : {}),
          })),
          use_cache:true,
        };
        const signature = JSON.stringify({...payload,idempotency_key:''});
        if (lastPayload.current !== signature) {
          key.current = createClientId(); payload.idempotency_key = key.current; lastPayload.current = signature;
        }
        const run = await evaluationApi.create(payload);
        onCreated(await evaluationApi.start(run.id));
      } catch (error) { setError(errorText(error)); }
      finally { setBusy(false); }
    }} onInvalidCapture={event => {
      // Native validation must be able to focus required custom fields inside the collapsed section.
      if (advanced.current?.contains(event.target as Node)) advanced.current.open = true;
    }}>
      <div className="workspace-dialog-body evaluation-setup-body">
        {loading && <p role="status">{t('加载中…')}</p>}
        <fieldset className="evaluation-fields" disabled={busy || !loaded}>
          <section className="evaluation-setup-section" aria-labelledby="evaluation-type-heading">
            <h3 id="evaluation-type-heading" className="label-with-tip">{t('评测类型')}<InfoTip label={t('评测类型说明')} text={t('RAG 评测检查最终回答及其依据；检索评测只检查召回内容和排序，不生成回答。')} /></h3>
            <div className="dataset-source-tabs" role="group" aria-label={t('评测类型')}>{(['rag','retrieval'] as EvaluationType[]).map(value=><button type="button" key={value} aria-pressed={evaluationType===value} disabled={!!fixedDataset?.evaluation_purpose && !['both',value].includes(fixedDataset.evaluation_purpose)} onClick={()=>changeType(value)}>{t(evaluationTypeLabels[value])}</button>)}</div>
          </section>
          <section className="evaluation-setup-section" aria-labelledby="evaluation-data-heading">
            <h3 id="evaluation-data-heading">{t(fixedDataset?'评测数据集':'1. 选择数据集')}</h3>
            {fixedDataset ? <div className="evaluation-fixed-dataset"><strong>{fixedDataset.name}</strong><span>{fixedDataset.item_count} {t('样本')}</span></div> : <label className="field"><span>{t('数据集')}</span>
              <select aria-label={t('数据集')} value={dataset} onChange={e => setDataset(e.target.value)}>
                {!compatibleDatasets.length && <option value="">{t('暂无适用的数据集')}</option>}
                {compatibleDatasets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.item_count} {t('样本')}</option>)}
              </select>
            </label>}
          </section>
          <section className="evaluation-setup-section" aria-labelledby="evaluation-target-heading">
            <h3 id="evaluation-target-heading">{t(fixedDataset?'1. 选择被测对象':'2. 选择被测对象')}</h3>
            <div className="evaluation-target-fields">
              <label className="field"><span>{t('对象类型')}</span>
                <select aria-label={t('被测目标类型')} value={benchmark} onChange={e => {
                  const value = e.target.value; setBenchmark(value); setTarget('');
                  setChosen(null);
                }}>
                  {evaluationType==='rag' && <option value="llm">{t('大语言模型')}</option>}
                  {evaluationType==='rag' && <option value="dify-chat">Dify Chat</option>}
                  {evaluationType==='retrieval' && <option value="dify-retrieve">{t('Dify 知识库')}</option>}
                </select>
              </label>
              <div className="field"><span>{t(evaluationType==='retrieval'?'被测知识库 / 检索服务':'被测应用 / 模型')}</span>
                {evaluationType==='retrieval'
                  ? <SearchableMultiSelect label={t('被测知识库 / 检索服务')} language={language} value={knowledgeTargets} disabled={busy || !loaded || !targets.length} options={targets} onChange={next=>{if(next.length<=20)setKnowledgeTargets(next);else setError(t('最多选择 20 个知识库'));}} />
                  : <SearchableSelect label={t('被测应用 / 模型')} language={language} value={target} disabled={busy || !loaded || !targets.length} options={targets} onChange={setTarget}/>}
              </div>
            </div>
            {loaded && !targets.length && <p className="evaluation-guidance" role="status">{t('没有可用的被测对象，请先在服务配置中添加模型或 Dify 配置。')}</p>}
            {evaluationType==='retrieval' && <>
              {!!knowledgeTargets.length && <ul className="evaluation-selected-targets">{knowledgeTargets.map(value=><li key={value}><span>{targets.find(option=>option.value===value)?.label || t('知识库配置不可用')}</span><button type="button" className="icon-button" disabled={busy} aria-label={`${t('移除来源')} ${targets.find(option=>option.value===value)?.label || ''}`} onClick={()=>setKnowledgeTargets(current=>current.filter(item=>item!==value))}><X aria-hidden="true"/></button></li>)}</ul>}
              {multiRoute ? <>
                <h4 className="label-with-tip">{t('多路召回策略')}<InfoTip label={t('多路召回策略说明')} text={t('各知识库分别召回候选片段，合并去重后统一重排，再按分数阈值筛选并保留最终 Top K。任一路失败时不会使用部分结果评分。')}/></h4>
                <div className="field"><span className="label-with-tip">{t('重排模型（Reranker）')}<InfoTip label={t('重排模型说明')} text={t('默认使用空间设置中的重排模型，可为本次评测单独更换。多选知识库时必须选择重排模型。')}/></span>
                  <SearchableSelect label={t('重排模型（Reranker）')} language={language} value={reranker} disabled={busy || !loaded} options={[{value:'',label:t('未选择重排模型')},...rerankers]} onChange={setReranker}/>
                  {!validReranker && <small className="error-text" role="alert">{t(reranker?'原重排模型已失效，请重新选择':'多路召回需要选择重排模型，或在空间设置中配置默认重排模型')}</small>}
                </div>
                <div className="evaluation-evidence-grid">
                  <label className="field"><span className="label-with-tip">{t('每库召回数量')}<InfoTip label={t('每库召回数量说明')} text={t('每个知识库提供的候选片段上限，范围 1–100，不能小于最终 Top K。候选片段越多，重排请求越大。')}/></span><input aria-label={t('每库召回数量')} type="number" required min={topK} max={100} value={candidateK} onChange={event=>setCandidateK(Number(event.target.value))}/></label>
                  <label className="field"><span className="label-with-tip">{t('最终 Top K')}<InfoTip label={t('最终 Top K 说明')} text={t('合并重排和阈值筛选后最多保留的结果数量，也用于 Recall@K、MRR 和 nDCG 的计算。')}/></span><input aria-label={t('最终 Top K')} type="number" required min={1} max={100} value={topK} onChange={event=>{const next=Number(event.target.value);setTopK(next);setCandidateK(current=>Math.max(current,next));}}/></label>
                  <label className="field"><span className="label-with-tip">{t('分数阈值（Score threshold）')}<InfoTip label={t('分数阈值说明')} text={t('范围 0–1，仅保留分数不低于阈值的结果。筛选后可能少于 Top K 或没有结果。')}/></span><input aria-label={t('分数阈值（Score threshold）')} type="number" required min={0} max={1} step={0.01} value={scoreThreshold} onChange={event=>setScoreThreshold(Number(event.target.value))}/></label>
                </div>
              </> : <p className="field-help">{t('单知识库将使用 Dify 当前保存的检索配置。')}</p>}
            </>}
          </section>
          <section className="evaluation-setup-section" aria-labelledby="evaluation-score-heading">
            <h3 id="evaluation-score-heading">{t(fixedDataset?'2. 确认评测方式':'3. 确认评测方式')}</h3>
              {evaluationType==='rag' && <label className="field"><span className="label-with-tip">{t('评测内容')}
              <InfoTip label={t('评测内容说明')} text={t('有参考答案时比较回答正确性；没有参考答案时可检查回答相关性或回答是否忠实于实际召回。')}/></span>
              <select aria-label={t('评测内容')} value={template} onChange={e => {setTemplate(e.target.value);setChosen(null);}}>
                <option value="reference_qa">{t('对照参考答案')}</option><option value="reference_free">{t('不使用参考答案')}</option>
              </select>
            </label>}
            <div className="evaluation-plan" role="status">
              <span>{t(chosen === null ? '推荐指标' : '已选指标')} · {engine === 'rules' ? t('确定性规则') : engine === 'ragas' ? 'RAGAS' : 'DeepEval'}</span>
              <strong>{effective.length ? effective.map(id => t(metricNames[id] || id)).join('、') : t('尚未选择指标')}</strong>
              {referenceRequired && <small>{t('所选指标需要参考答案。若数据尚未补充答案，请先在数据集中手工添加、导入，或开启“生成参考答案”后构建新数据。缺失参考答案的样本将标为不可计算，不会自动生成答案。')}</small>}
              {retrievalRequired && <small>{t('需要应用返回实际召回内容；参考资料不能替代实际召回。')}</small>}
            </div>
            {loaded && !effective.length && <p className="evaluation-guidance" role="status">{t(
              engine === 'ragas' && template === 'reference_free' && benchmark === 'llm'
                ? 'RAGAS 当前的无参考指标需要实际召回。可改用参考答案，或在高级设置中切换为 DeepEval。'
                : '请在高级设置中选择适用的评分引擎和指标。')}</p>}
            <label className="field"><span className="label-with-tip">{t('结果评分引擎')}<InfoTip label={t('评分引擎说明')} text={t('选择用于计算本次评测指标的执行引擎。DeepEval 和 RAGAS 负责模型评分，确定性规则用于 Recall@K、MRR、nDCG 等可直接计算的指标。')}/></span>
              <select aria-label={t('结果评分引擎')} value={engine} onChange={e => {setEngine(e.target.value);setChosen(null);}}>
                <option value="deepeval">DeepEval 4.3.2</option><option value="ragas">RAGAS 0.4.3</option><option value="rules">{t('确定性规则')}</option>
              </select>
            </label>
            {needsJudge && <div className="field"><span className="label-with-tip">{t('评审模型')}
              <InfoTip label={t('评审模型说明')} text={t('负责按所选指标评分，默认沿用空间设置中的结果评分模型。不会自动使用被测模型。')}/></span>
              <SearchableSelect label={t('评审模型')} language={language} value={judge} disabled={busy || !loaded || !llms.length}
                options={[{value:'',label:t('请选择评分模型')},...llms]} onChange={setJudge}/>
            </div>}
            {needsJudge && !validJudge && loaded && <p className="evaluation-guidance" role="status">{t(llms.length
              ? '请选择一个评审模型后开始评测。' : '没有可用的评审模型，请先在服务配置中添加大语言模型。')}</p>}
            <div className="evaluation-evidence-grid">
              <label className="field"><span className="label-with-tip">{t('运行范围')}<InfoTip label={t('运行范围说明')} text={t('试跑最多使用所选条数；全部样本仍受空间设置中的预算限制。')}/></span>
                <select aria-label={t('运行范围')} value={scope} onChange={e => setScope(e.target.value)}>
                  <option value="5">{t('先试跑 5 条（推荐）')}</option><option value="20">{t('先试跑 20 条')}</option>
                  <option value="all">{t('全部样本')}</option><option value="custom">{t('自定义条数')}</option>
                </select>
              </label>
              {scope === 'custom' && <label className="field"><span>{t('样本数上限')}</span>
                <input type="number" required min="1" max="20000" value={customLimit} onChange={e => setCustomLimit(e.target.value)}/>
              </label>}
            </div>
          </section>
          <details ref={advanced} className="evaluation-advanced">
            <summary>{t('高级设置')}<span>{t('指标、提示词与预算')}</span></summary>
            <div className="evaluation-fields">
              <div className="evaluation-evidence-grid">
                <label className="field"><span>{t('评测名称')}</span><input value={name} placeholder={t('留空自动命名')} onChange={e => setName(e.target.value)} maxLength={120}/></label>
              </div>
              <fieldset className="evaluation-metrics"><legend>{t('选择指标')}</legend>
                {available.map(metric => <div className="evaluation-metric-row" key={metric.id}>
                  <label><input type="checkbox" checked={effective.includes(metric.id)} onChange={e => setChosen(e.target.checked ? [...effective,metric.id] : effective.filter(id => id !== metric.id))}/>
                    <span><span className="label-with-tip">{t(metricNames[metric.id] || metric.id)}{metricHelp[metric.id] && <InfoTip label={t('指标说明')} text={t(metricHelp[metric.id])}/>}</span><small>{metric.engine} · {t('需要')} {metric.required_fields.map(field => t(fieldNames[field] || field)).join(', ')}</small></span>
                  </label>
                  {effective.includes(metric.id) && <label className="evaluation-threshold"><span>{t('通过阈值')} · {thresholds[metric.id] ?? metric.threshold.default}</span>
                    <input type="range" aria-label={t(metricNames[metric.id] || metric.id)+' '+t('通过阈值')} min="0" max="1" step="0.05" value={thresholds[metric.id] ?? metric.threshold.default} onChange={e => setThresholds({...thresholds,[metric.id]:Number(e.target.value)})}/>
                  </label>}
                </div>)}
                <button className="ghost" type="button" onClick={() => {setChosen(null);setThresholds({});}}>{t('恢复推荐指标')}</button>
              </fieldset>
              {effective.includes('deepeval.custom') && <>
                <label className="field"><span>{t('业务评分规则')}</span><textarea required value={criteria} onChange={e => setCriteria(e.target.value)} maxLength={10000}/></label>
                <button className="ghost" type="button" onClick={() => setCriteria(t('必须先给出明确结论，再列出依据；涉及未知信息时应说明不确定，禁止编造金额和日期。'))}>{t('填入规则示例')}</button>
                <label className="field"><span>{t('评分示例 / Rubric')}</span><textarea value={examples} onChange={e => setExamples(e.target.value)} maxLength={10000}/></label>
              </>}
              {effective.includes('rules.required_text') && <label className="field"><span>{t('必需短语（每行一个）')}</span><textarea required value={phrases} onChange={e => setPhrases(e.target.value)}/></label>}
              {effective.includes('rules.json_schema') && <label className="field"><span>JSON Schema</span><textarea required value={schema} onChange={e => setSchema(e.target.value)}/></label>}
              {benchmark === 'llm' && <label className="field"><span>{t('目标系统提示词')}</span><textarea value={prompt} onChange={e => setPrompt(e.target.value)} maxLength={20000}/></label>}
              <p className="field-help">{t('请求和 token 预算由空间设置统一管理。')}</p>
            </div>
          </details>
        </fieldset>
        {error && <div className="alert" role="alert">{error}</div>}
        {!loaded && !loading && <button className="ghost" type="button" onClick={() => setReload(value => value+1)}>{t('重新加载')}</button>}
        {loaded && !compatibleDatasets.length && <p role="status">{t('请先创建或修改为对应用途或两者通用的数据集，并添加启用样本。')}</p>}
      </div>
      <footer className="evaluation-setup-footer">
        <span>{t('自动保存数据快照，方便复现结果。')}</span>
        <div className="workspace-dialog-actions"><button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('取消')}</button>
          <button className="primary" disabled={busy || !canSubmit}><Play aria-hidden="true"/>{buttonText}</button>
        </div>
      </footer>
    </form>
  </WorkspaceDialog>;
}
