import { useEffect, useState } from 'react';
import { ChevronRight, RotateCcw, Save } from 'lucide-react';
import type { Catalog } from '../types';
import { tr, type Language } from '../i18n';
import { datasetApi, embeddingChoices, llmChoices, rerankerChoices, selectionValue } from '../datasetApi';
import { SearchableSelect } from './SearchableSelect';
import { InfoTip } from './InfoTip';

type SettingsSection = 'generation' | 'evaluation';
function PromptEditor({ label, tip, value, defaultValue, disabled, readOnly, language, onChange }: {
  label: string; tip: string; value: string; defaultValue: string; disabled: boolean; readOnly: boolean; language: Language; onChange: (value: string) => void;
}) {
  const t = (value: string) => tr(language, value);
  return <div className="field space-settings-prompt">
    <div className="space-settings-prompt-head"><span className="label-with-tip">{t(label)}<InfoTip label={t('查看说明')} text={t(tip)} /></span>
      {!readOnly && <button type="button" className="ghost compact" disabled={disabled || value === defaultValue} onClick={() => onChange(defaultValue)} aria-label={`${t('恢复默认')} · ${t(label)}`}><RotateCcw aria-hidden="true" />{t('恢复默认')}</button>}
    </div>
    <textarea aria-label={t(label)} rows={7} maxLength={20000} disabled={disabled} value={value} onChange={event => onChange(event.target.value)} />
    {!disabled && !value.trim() && <small className="error-text" role="alert">{t('提示词不能为空，可恢复默认后保存。')}</small>}
  </div>;
}

export function DatasetSettings({ catalog, language, readOnly, section = 'generation' }: { catalog: Catalog | null; language: Language; readOnly: boolean; section?: string }) {
  const t = (value: string) => tr(language, value);
  const [selected, setSelected] = useState('');
  const [embedding, setEmbedding] = useState('');
  const [critic, setCritic] = useState('');
  const [concurrency, setConcurrency] = useState('3');
  const [judge, setJudge] = useState('');
  const [reranker, setReranker] = useState('');
  const [scoringEngine, setScoringEngine] = useState<'deepeval' | 'ragas'>('deepeval');
  const [judgePrompt, setJudgePrompt] = useState('');
  const [defaultJudgePrompt, setDefaultJudgePrompt] = useState('');
  const [evaluationMaxRequests, setEvaluationMaxRequests] = useState('500');
  const [evaluationMaxOutputTokens, setEvaluationMaxOutputTokens] = useState('1000000');
  const [evaluationConcurrency, setEvaluationConcurrency] = useState('3');
  const [prompt, setPrompt] = useState('');
  const [defaultPrompt, setDefaultPrompt] = useState('');
  const [criticPrompt, setCriticPrompt] = useState('');
  const [defaultCriticPrompt, setDefaultCriticPrompt] = useState('');
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<SettingsSection | null>(null);
  const [loadError, setLoadError] = useState('');
  const [errors, setErrors] = useState({ generation: '', evaluation: '' });
  const [saved, setSaved] = useState({ generation: false, evaluation: false });
  const [reload, setReload] = useState(0);
  const choices = llmChoices(catalog, language);
  const embeddings = embeddingChoices(catalog, language);
  const rerankers = rerankerChoices(catalog, language);
  const staleReranker = !!catalog && !!reranker && !rerankers.some(choice => choice.value === reranker);
  const stale = !!catalog && !!selected && !choices.some(choice => choice.value === selected);
  const staleEmbedding = !!catalog && !!embedding && !embeddings.some(choice => choice.value === embedding);
  const staleJudge = !!catalog && !!judge && !choices.some(choice => choice.value === judge);
  const staleCritic = !!catalog && !!critic && !choices.some(choice => choice.value === critic);
  const disabled = readOnly || !catalog || !loaded || loading || busy !== null;
  const invalidConcurrency = !/^\d+$/.test(concurrency) || Number(concurrency) < 1 || Number(concurrency) > 10;
  const invalidEvaluationMaxRequests = !/^\d+$/.test(evaluationMaxRequests) || Number(evaluationMaxRequests) < 1 || Number(evaluationMaxRequests) > 100000;
  const invalidEvaluationMaxOutputTokens = !/^\d+$/.test(evaluationMaxOutputTokens) || Number(evaluationMaxOutputTokens) < 256 || Number(evaluationMaxOutputTokens) > 100000000;
  const invalidEvaluationConcurrency = !/^\d+$/.test(evaluationConcurrency) || Number(evaluationConcurrency) < 1 || Number(evaluationConcurrency) > 10;
  const changed = (which: SettingsSection) => { setSaved(value => ({ ...value, [which]: false })); setErrors(value => ({ ...value, [which]: '' })); };
  useEffect(() => {
    let active = true;
    setLoading(true); setLoadError('');
    datasetApi.settings().then(value => { if (active) {
      setSelected(selectionValue(value.default_llm)); setEmbedding(selectionValue(value.default_embedding_model)); setCritic(selectionValue(value.default_critic_llm));
      setConcurrency(String(value.max_concurrent ?? 3));
      setPrompt(value.prompt); setDefaultPrompt(value.default_prompt); setCriticPrompt(value.critic_prompt); setDefaultCriticPrompt(value.default_critic_prompt);
      setJudge(selectionValue(value.default_judge_llm)); setScoringEngine(value.scoring_engine); setJudgePrompt(value.judge_prompt); setDefaultJudgePrompt(value.default_judge_prompt); setLoaded(true);
      setReranker(selectionValue(value.default_reranker || null));
      setEvaluationMaxRequests(String(value.evaluation_max_requests ?? 500));
      setEvaluationMaxOutputTokens(String(value.evaluation_max_output_tokens ?? 1000000));
      setEvaluationConcurrency(String(value.evaluation_max_concurrent ?? 3));
    } }).catch(reason => { if (active) setLoadError(reason.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reload]);
  async function save(which: SettingsSection) {
    if (disabled || (which === 'generation' ? stale || staleEmbedding || staleCritic || invalidConcurrency || !prompt.trim() || !criticPrompt.trim() : staleJudge || staleReranker || invalidEvaluationConcurrency || invalidEvaluationMaxRequests || invalidEvaluationMaxOutputTokens || !judgePrompt.trim())) return;
    setBusy(which); changed(which);
    try {
      await datasetApi.saveSettings(which === 'generation' ? {
        max_concurrent: Number(concurrency),
        default_llm: selected ? JSON.parse(selected) : null,
        default_embedding_model: embedding ? JSON.parse(embedding) : null,
        default_critic_llm: critic ? JSON.parse(critic) : null,
        prompt: prompt === defaultPrompt ? null : prompt,
        critic_prompt: criticPrompt === defaultCriticPrompt ? null : criticPrompt,
      } : {
        evaluation_max_concurrent: Number(evaluationConcurrency),
        evaluation_max_requests: Number(evaluationMaxRequests),
        evaluation_max_output_tokens: Number(evaluationMaxOutputTokens),
        default_judge_llm: judge ? JSON.parse(judge) : null,
        default_reranker: reranker ? JSON.parse(reranker) : null,
        scoring_engine: scoringEngine,
        judge_prompt: judgePrompt === defaultJudgePrompt ? null : judgePrompt,
      });
      setSaved(value => ({ ...value, [which]: true }));
    } catch (reason) { setErrors(value => ({ ...value, [which]: reason instanceof Error ? reason.message : String(reason) })); }
    finally { setBusy(null); }
  }
  const feedback = (which: SettingsSection) => <>
    {(loading || !catalog) && <p className="workspace-muted" role="status">{t('加载中…')}</p>}
    {loadError && <div className="alert" role="alert">{loadError}<button type="button" className="ghost compact" disabled={loading} onClick={() => setReload(value => value + 1)}>{t('重试')}</button></div>}
    {errors[which] && <div className="alert" role="alert">{errors[which]}</div>}
        <div className="space-settings-footer">{!readOnly && <button className="primary compact" disabled={disabled || (which === 'generation' ? stale || staleEmbedding || staleCritic || invalidConcurrency || !prompt.trim() || !criticPrompt.trim() : staleJudge || staleReranker || invalidEvaluationConcurrency || invalidEvaluationMaxRequests || invalidEvaluationMaxOutputTokens || !judgePrompt.trim())}><Save aria-hidden="true" />{t(busy === which ? '保存中…' : '保存配置')}</button>}{saved[which] && <span className="success-text" role="status">{t('空间配置已保存')}</span>}</div>
  </>;
  return <>
    <section className="panel system-settings-panel dataset-settings-panel" role="tabpanel" id="space-panel-generation" aria-labelledby="space-tab-generation" hidden={section !== 'generation'}>
      <div className="panel-head"><div><h2>{t('数据集生成')}</h2><p>{t('为文档和 Dify 知识库生成评测问答，可在构建时覆盖默认模型。')}</p></div></div>
      <form className="system-settings-form" onSubmit={event => { event.preventDefault(); void save('generation'); }}>
        <div className="field"><span className="label-with-tip">{t('数据集生成模型')}<InfoTip label={t('查看说明')} text={t('负责生成问题和参考答案，作为当前空间新建数据集的默认模型；可在生成时单独更换。')} /></span>
          <SearchableSelect label={t('数据集生成模型')} language={language} value={selected} disabled={disabled} onChange={value => { setSelected(value); changed('generation'); }} options={[{ value: '', label: t('未设置（构建时选择）') }, ...choices]} />
          {stale && <small className="error-text" role="alert">{t('原默认模型已失效，请重新选择')}</small>}
          {catalog && !choices.length && <small className="field-help">{t('暂无已配置的 LLM，请先在模型供应商中添加。')}</small>}
        </div>
        <div className="field"><span className="label-with-tip">{t('默认嵌入模型')}<span className="space-settings-tag">RAGAS</span><InfoTip label={t('查看说明')} text={t('用于 RAGAS 根据资料构建知识图谱和生成样本。构建时可单独选择嵌入模型，未选择时使用此配置。保存配置不会调用模型。')} /></span>
          <SearchableSelect label={t('默认嵌入模型')} language={language} value={embedding} disabled={disabled} onChange={value => { setEmbedding(value); changed('generation'); }} options={[{ value: '', label: t('未设置（构建时选择）') }, ...embeddings]} />
          {staleEmbedding && <small className="error-text" role="alert">{t('原嵌入模型已失效，请重新选择')}</small>}
          {catalog && !embeddings.length && <small className="field-help">{t('暂无已配置的嵌入模型，请先在模型供应商中添加。')}</small>}
        </div>
        <div className="field"><span className="label-with-tip">{t('数据集生成质量评审模型')}<span className="space-settings-tag">{t('仅 DeepEval')}</span><InfoTip label={t('查看说明')} text={t('用于评估初始问题的清晰度、独立完整性和可回答性，不代表最终答案正确性。未单独设置时使用生成模型；评审也会产生模型调用费用。')} /></span>
          <SearchableSelect label={t('数据集生成质量评审模型')} language={language} value={critic} disabled={disabled} onChange={value => { setCritic(value); changed('generation'); }} options={[{ value: '', label: t('与生成模型相同') }, ...choices]} />
          {staleCritic && <small className="error-text" role="alert">{t('原质量评审模型已失效，请重新选择')}</small>}
        </div>
        <label className="field"><span className="label-with-tip">{t('模型并发上限')}<span className="space-settings-tag">DeepEval / RAGAS</span><InfoTip label={t('模型并发上限说明')} text={t('范围 1–10，默认 3。控制每个数据集生成任务的模型并发。新建、追加、重新构建及失败重试统一使用当前空间配置，不支持在数据集内覆盖；已运行的任务不受影响。较大可能触发服务限流。')} /></span>
          <input type="number" aria-label={t('模型并发上限')} min={1} max={10} step={1} disabled={disabled} value={concurrency} onChange={event => { setConcurrency(event.target.value); changed('generation'); }} />
          {loaded && invalidConcurrency && <small className="error-text" role="alert">{t('请输入 1–10 之间的整数。')}</small>}
        </label>
        <details className="space-settings-advanced"><summary><ChevronRight aria-hidden="true"/><span>{t('提示词配置')}</span><small>{t(prompt === defaultPrompt && criticPrompt === defaultCriticPrompt ? '使用默认提示词' : '已自定义')}</small></summary>
          <div className="space-settings-advanced-body">
            <PromptEditor label="构建提示词" tip="构建提示词用于 DeepEval 和 RAGAS 的生成任务要求，保存后用于新构建。" value={prompt} defaultValue={defaultPrompt} disabled={disabled} readOnly={readOnly} language={language} onChange={value => { setPrompt(value); changed('generation'); }}/>
            <PromptEditor label="数据集生成质量评审提示词" tip="补充初始问题的评审标准，用于清晰度、独立完整性和可回答性评分，不验证参考答案的事实正确性。仅有参考上下文的生成流程使用；评分与输出结构由 SDK 控制。保存后用于新构建，失败重试沿用原配置。" value={criticPrompt} defaultValue={defaultCriticPrompt} disabled={disabled} readOnly={readOnly} language={language} onChange={value => { setCriticPrompt(value); changed('generation'); }}/>
          </div>
        </details>
        {loaded && (!prompt.trim() || !criticPrompt.trim()) && <p className="error-text" role="alert">{t('请在提示词配置中填写内容，或恢复默认后保存。')}</p>}
        {feedback('generation')}
      </form>
    </section>
    <section className="panel system-settings-panel dataset-settings-panel" role="tabpanel" id="space-panel-evaluation" aria-labelledby="space-tab-evaluation" hidden={section !== 'evaluation'}>
      <div className="panel-head"><div><h2>{t('质量评测默认配置')}</h2><p>{t('配置当前空间 RAG 与检索评测的默认模型、引擎与评分要求。')}</p></div></div>
      <form className="system-settings-form" onSubmit={event => { event.preventDefault(); void save('evaluation'); }}>
        <label className="field"><span className="label-with-tip">{t('结果评分引擎')}<InfoTip label={t('查看说明')} text={t('独立于数据集生成后端。支持 DeepEval 与 RAGAS 官方指标，评分时使用独立的结果评分模型。')} /></span><select aria-label={t('结果评分引擎')} disabled={disabled} value={scoringEngine} onChange={event => { setScoringEngine(event.target.value as 'deepeval' | 'ragas'); changed('evaluation'); }}><option value="deepeval">DeepEval</option><option value="ragas">RAGAS</option></select></label>
        <div className="field"><span className="label-with-tip">{t('结果评分模型')}<InfoTip label={t('查看说明')} text={t('评估被测应用的实际回答，与生成模型和生成质量评审模型分别配置。未设置时需在准备评测时选择。')} /></span>
          <SearchableSelect label={t('结果评分模型')} language={language} value={judge} disabled={disabled} onChange={value => { setJudge(value); changed('evaluation'); }} options={[{ value: '', label: t('未设置（准备评测时选择）') }, ...choices]} />
          {staleJudge && <small className="error-text" role="alert">{t('原结果评分模型已失效，请重新选择')}</small>}
        </div>
        <div className="field"><span className="label-with-tip">{t('默认重排模型（Reranker）')}<InfoTip label={t('查看说明')} text={t('用于检索评测合并多个知识库的召回结果后统一重排。新建评测时默认选中此模型，也可单独更换；保存配置不会调用模型。')} /></span>
          <SearchableSelect label={t('默认重排模型（Reranker）')} language={language} value={reranker} disabled={disabled} onChange={value => { setReranker(value); changed('evaluation'); }} options={[{value:'',label:t('未设置（准备评测时选择）')},...rerankers]} />
          {staleReranker && <small className="error-text" role="alert">{t('原重排模型已失效，请重新选择')}</small>}
          {catalog && !rerankers.length && <small className="field-help">{t('暂无已配置的重排模型，请先在模型供应商中添加。')}</small>}
        </div>
        <div className="evaluation-evidence-grid">
          <label className="field"><span className="label-with-tip">{t('评测并发数')}<InfoTip label={t('并发设置说明')} text={t('控制同一条评测同时处理的样本数。范围 1–10，默认 3；新建评测时冻结，已运行的评测不受修改影响。数值越大完成越快，但可能触发目标服务或评分模型限流。')} /></span><input required type="number" min="1" max="10" step="1" disabled={disabled} value={evaluationConcurrency} onChange={event => { setEvaluationConcurrency(event.target.value); changed('evaluation'); }} />{loaded && invalidEvaluationConcurrency && <small className="error-text">{t('请输入 1–10 之间的整数。')}</small>}</label>
          <label className="field"><span className="label-with-tip">{t('请求总数上限')}<InfoTip label={t('预算说明')} text={t('空间默认的单次评测请求总数上限，新建评测时自动使用。')} /></span><input required type="number" min="1" max="100000" disabled={disabled} value={evaluationMaxRequests} onChange={event => { setEvaluationMaxRequests(event.target.value); changed('evaluation'); }} />{loaded && invalidEvaluationMaxRequests && <small className="error-text">{t('请输入 1–100000 之间的整数。')}</small>}</label>
          <label className="field"><span className="label-with-tip">{t('输出 token 预留预算')}<InfoTip label={t('预算说明')} text={t('空间默认的单次评测输出 token 预留预算，新建评测时自动使用。')} /></span><input required type="number" min="256" max="100000000" disabled={disabled} value={evaluationMaxOutputTokens} onChange={event => { setEvaluationMaxOutputTokens(event.target.value); changed('evaluation'); }} />{loaded && invalidEvaluationMaxOutputTokens && <small className="error-text">{t('请输入 256–100000000 之间的整数。')}</small>}</label>
        </div>
        <details className="space-settings-advanced"><summary><ChevronRight aria-hidden="true"/><span>{t('提示词配置')}</span><small>{t(judgePrompt === defaultJudgePrompt ? '使用默认提示词' : '已自定义')}</small></summary>
          <div className="space-settings-advanced-body"><PromptEditor label="结果评分提示词" tip="补充回答或检索结果的评分要求；准备评测时冻结到运行快照，不影响生成质量评审。" value={judgePrompt} defaultValue={defaultJudgePrompt} disabled={disabled} readOnly={readOnly} language={language} onChange={value => { setJudgePrompt(value); changed('evaluation'); }}/></div>
        </details>
        {loaded && !judgePrompt.trim() && <p className="error-text" role="alert">{t('请在提示词配置中填写内容，或恢复默认后保存。')}</p>}
        {feedback('evaluation')}
      </form>
    </section>
  </>;
}
