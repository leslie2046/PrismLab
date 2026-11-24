import { useEffect, useState } from 'react';
import { FlaskConical } from 'lucide-react';
import type { DatasetItem, GenerationDetails as GenerationData } from '../types';
import { datasetApi } from '../datasetApi';
import { tr, type Language } from '../i18n';
import { WorkspaceDialog } from './WorkspaceDialog';

export type GenerationTab = 'prompts' | 'context';
type GenerationTrace = NonNullable<GenerationData['trace']>;
type GenerationStep = NonNullable<GenerationTrace['llm_requests']>[number]
  & Pick<NonNullable<GenerationTrace['llm_request']>, 'system_prompt' | 'provider_id' | 'credential_id'>;

const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const parameterText = (value: unknown, defaultLabel: string) => value == null ? defaultLabel : typeof value === 'string' ? value : JSON.stringify(value);
const playgroundParameters = (raw: Record<string, unknown> = {}) => {
  const result: Record<string, string | number> = {};
  for (const key of ['max_tokens', 'temperature', 'top_p', 'thinking_budget', 'reasoning_effort']) {
    const value = raw[key];
    if (typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value))) result[key] = value;
  }
  const thinking = raw.thinking;
  if (typeof thinking === 'object' && thinking !== null && typeof (thinking as {type?: unknown}).type === 'string') result.thinking_mode = String((thinking as {type: string}).type);
  if (typeof raw.enable_thinking === 'boolean') result.thinking_mode = raw.enable_thinking ? 'enabled' : 'disabled';
  const templateThinking = typeof raw.chat_template_kwargs === 'object' && raw.chat_template_kwargs !== null ? (raw.chat_template_kwargs as {enable_thinking?: unknown}).enable_thinking : undefined;
  if (typeof templateThinking === 'boolean') result.thinking_mode = templateThinking ? 'enabled' : 'disabled';
  return result;
};

export function GenerationDetails({datasetId, item, initialTab, language, onClose, onPlayground}: {datasetId: string; item: DatasetItem; initialTab: GenerationTab; language: Language; onClose: () => void; onPlayground?: (draft: {systemPrompt: string; query: string; providerId?: string; model?: string; credentialId?: string; parameters: Record<string, string | number>}) => void}) {
  const t = (text: string) => tr(language, text);
  const [tab, setTab] = useState(initialTab);
  const [data, setData] = useState<GenerationData | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(''); setData(null);
    datasetApi.generation(datasetId, item.id, controller.signal).then(value => {if (!controller.signal.aborted) setData(value);}).catch(reason => {
      if (!controller.signal.aborted) setError(errorText(reason));
    });
    return () => controller.abort();
  }, [datasetId, item.id, reload]);
  const recorded = data?.availability === 'recorded';
  const contexts = data?.sample.context || [];
  const trace = data?.trace;
  const requestSteps: GenerationStep[] = trace?.llm_requests?.length ? trace.llm_requests : trace?.llm_request ? [{
    ...trace.llm_request,
    sequence: trace.llm_request.request_sequence,
    role: trace.llm_request.request_role || 'generation',
  }] : [];
  const stepLabel = (role?: string) => role === 'quality_review' ? t('审查') : role === 'rewrite' ? t('改写') : role === 'answer_generation' ? t('回答生成') : t('生成');
  function openStepInPlayground(request: GenerationStep, query: string) {
    if (!trace || !onPlayground) return;
    const reviewing = request.role === 'quality_review';
    const snapshot = reviewing ? trace.models.critic : trace.models.generator;
    const options = trace.options?.[reviewing ? 'critic_llm' : 'llm'];
    const selection = snapshot?.selection
      ?? (options && typeof options === 'object' ? options as NonNullable<typeof snapshot>['selection'] : undefined)
      ?? (reviewing && !snapshot ? trace.models.generator?.selection : undefined);
    // The catalog uses configured aliases, which can differ from the wire model name.
    const model = snapshot?.model && request.model && snapshot.model !== request.model
      ? request.model : selection?.model || request.model || snapshot?.model;
    onPlayground({
      systemPrompt: request.messages?.find(message => message.role === 'system')?.content ?? request.system_prompt ?? '',
      query,
      providerId: selection?.id || (reviewing ? undefined : request.provider_id),
      model,
      credentialId: selection?.credential_id || (reviewing ? undefined : request.credential_id),
      parameters: playgroundParameters(request.parameters),
    });
    onClose();
  }

  return <WorkspaceDialog className="dataset-generation-dialog" title={t('样本生成详情')} busy={false} language={language} onClose={onClose}>
    <div className="workspace-dialog-body dataset-generation-body">
      <p className="generation-question">{item.input}</p>
      <div className="dataset-local-tabs generation-tabs" role="tablist" aria-label={t('生成追溯')} onKeyDown={event => {
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'prompts' : event.key === 'End' ? 'context' : tab === 'prompts' ? 'context' : 'prompts';
        setTab(next); document.getElementById(`generation-${next}-tab`)?.focus();
      }}>
        {(['prompts', 'context'] as const).map(value => <button key={value} type="button" role="tab" id={`generation-${value}-tab`} aria-controls={`generation-${value}-panel`} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)}>{t(value === 'prompts' ? '生成提示词' : '上下文与来源')}</button>)}
      </div>
      {error ? <div className="generation-load-error" role="alert"><p className="error-text">{error}</p><button type="button" className="ghost" onClick={() => setReload(value => value + 1)}>{t('重试')}</button></div> : !data ? <p role="status">{t('加载中…')}</p> : <>
        {!recorded && <p className="generation-notice" role="status">{t('此样本未保存生成请求记录，无法还原当时的提示词。下方仅展示样本已保存的上下文。')}</p>}
        {data.edited && <p className="generation-notice">{t('此样本生成后已被编辑。下方展示生成时的原始快照。')}</p>}
        {recorded && trace && <p className="generation-meta"><time dateTime={trace.created_at}>{new Date(trace.created_at).toLocaleString(language)}</time><span>{trace.backend === 'ragas_native' ? 'RAGAS' : trace.backend === 'ragas' ? 'RAGAS compatible' : 'DeepEval'}</span>{trace.models.generator?.model && <span>{trace.models.generator.model}</span>}</p>}
        {recorded && trace?.llm_request?.request_scope === 'batch' && <p className="generation-notice" role="status">{t('这是批次模型请求快照；一次请求可能服务同批多个样本。当前样本的真实上下文请以“上下文与来源”中的保存内容为准。')}</p>}
        {recorded && trace?.llm_request?.request_match === 'batch_snapshot' && <p className="generation-notice" role="status">{t('未在已保存的模型请求中找到当前样本的精确问题或上下文匹配，可能是旧版批量记录。')}</p>}
        {tab === 'prompts' && <div role="tabpanel" id="generation-prompts-panel" aria-labelledby="generation-prompts-tab">
          {trace ? <>
            <section className="generation-section">
              <h3>{t('生成步骤')}</h3>
              <div className="generation-step-list">{requestSteps.map((request, index) => {
                const label = `${index + 1}. ${stepLabel(request.role)}`;
                const userMessage = request.messages?.find(message => message.role === 'user')?.content;
                return <article className="generation-step" key={`${request.sequence ?? index}-${request.role}`}>
                  <header>
                    <strong>{label}</strong>
                    <div className="generation-step-actions">
                      {request.model && <span>{request.model}</span>}
                      {onPlayground && <button type="button" className="ghost compact" disabled={!userMessage}
                        aria-label={`${label} · ${t('到Playground调试')}`}
                        onClick={() => userMessage && openStepInPlayground(request, userMessage)}
                      ><FlaskConical aria-hidden="true" />{t('到Playground调试')}</button>}
                    </div>
                  </header>
                  <div className="generation-message-list">{(request.messages || []).map((message, messageIndex) => (
                    <article className={`generation-message-card generation-message-${message.role}`} key={`${message.role}-${messageIndex}`}>
                      <strong>{message.role}</strong><pre>{message.content}</pre>
                    </article>
                  ))}</div>
                  {request.parameters && <details><summary>{t('调用参数')}</summary><pre>{JSON.stringify(request.parameters, null, 2)}</pre></details>}
                </article>;
              })}</div>
            </section>
            <section className="generation-section"><h3>{t('调用大模型参数')}</h3>{trace.llm_request ? <>
              <dl className="generation-parameter-list"><div><dt>{t('模型')}</dt><dd>{trace.llm_request.model || '—'}</dd></div>{Object.entries(trace.llm_request.parameter_details || trace.llm_request.parameters || {}).map(([name, detail]) => {
                const value = typeof detail === 'object' && detail !== null && 'value' in detail ? (detail as {value?: unknown}).value : detail;
                return <div key={name}><dt>{name}</dt><dd>{parameterText(value, t('服务默认值'))}</dd></div>;
              })}</dl>
            </> : <p className="field-help">{t('当前历史记录未保存实际调用参数。')}</p>}</section>
            {trace.backend === 'deepeval' && trace.critic_prompt && <details className="generation-section"><summary>{t('质量评审提示词快照')}</summary><pre>{trace.critic_prompt}</pre></details>}
          </> : <p className="field-help">{t('未记录生成提示词')}</p>}
        </div>}
        {tab === 'context' && <div role="tabpanel" id="generation-context-panel" aria-labelledby="generation-context-tab">
          {data.edited && <details className="generation-section"><summary>{t('生成时的原始问题与答案')}</summary><pre>{data.sample.input}</pre><pre>{data.sample.expected_output || t('未设置参考答案')}</pre></details>}
          <section className="generation-section"><h3>{t(recorded ? '生成时参考上下文' : '样本保存的上下文')} ({contexts.length})</h3>{contexts.length ? contexts.map((context, index) => <details key={index} className="generation-context" open={contexts.length === 1}><summary>{t('参考片段')} {index + 1}</summary><pre>{context}</pre></details>) : <p className="field-help">{t('此样本没有保存参考上下文。')}</p>}</section>
        </div>}
      </>}
    </div>
    <div className="workspace-dialog-footer"><button type="button" className="ghost" onClick={onClose}>{t('关闭')}</button></div>
  </WorkspaceDialog>;
}
