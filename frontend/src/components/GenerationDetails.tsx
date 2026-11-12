import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { DatasetItem, GenerationDetails as GenerationData, GenerationRequest, GenerationRequestSummary } from '../types';
import type { PageResult } from '../pagination';
import { datasetApi } from '../datasetApi';
import { tr, type Language } from '../i18n';
import { WorkspaceDialog } from './WorkspaceDialog';
import { Pagination } from './Pagination';

export type GenerationTab = 'prompts' | 'context';
const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export function GenerationDetails({datasetId, item, initialTab, language, onClose}: {datasetId: string; item: DatasetItem; initialTab: GenerationTab; language: Language; onClose: () => void}) {
  const t = (text: string) => tr(language, text);
  const [tab, setTab] = useState(initialTab);
  const [data, setData] = useState<GenerationData | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [sourceLimit, setSourceLimit] = useState(20);
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
        {tab === 'prompts' && <div role="tabpanel" id="generation-prompts-panel" aria-labelledby="generation-prompts-tab">
          {trace ? <>
            <section className="generation-section"><h3>{t('生成任务提示词快照')}</h3><pre>{trace.prompt}</pre>{typeof trace.options.task === 'string' && trace.options.task && <pre>{trace.options.task}</pre>}</section>
            {trace.backend === 'deepeval' && trace.critic_prompt && <details className="generation-section"><summary>{t('质量评审提示词快照')}</summary><pre>{trace.critic_prompt}</pre></details>}
            <section className="generation-section"><h3>{t('同批次模型请求')}</h3><p className="field-help">{t('包含该样本所在批次的生成、评审和改写请求，以及失败尝试。展开可查看实际请求消息；批量调用不代表每个请求仅对应这一条样本。')}</p><TraceRequests datasetId={datasetId} itemId={item.id} language={language} /></section>
          </> : <p className="field-help">{t('未记录生成提示词')}</p>}
        </div>}
        {tab === 'context' && <div role="tabpanel" id="generation-context-panel" aria-labelledby="generation-context-tab">
          {data.edited && <details className="generation-section"><summary>{t('生成时的原始问题与答案')}</summary><pre>{data.sample.input}</pre><pre>{data.sample.expected_output || t('未设置参考答案')}</pre></details>}
          <section className="generation-section"><h3>{t(recorded ? '生成时参考上下文' : '样本保存的上下文')} ({contexts.length})</h3>{contexts.length ? contexts.map((context, index) => <details key={index} className="generation-context" open={contexts.length === 1}><summary>{t('参考片段')} {index + 1}</summary><pre>{context}</pre></details>) : <p className="field-help">{t('此样本没有保存参考上下文。')}</p>}</section>
          {trace && <section className="generation-section"><h3>{t('本批次来源快照')} ({trace.sources.length})</h3><p className="field-help">{t('以下资料保存于本次生成时，后续同步知识库不会改变这些快照。')}</p>{trace.sources.length ? <>{trace.sources.slice(0, sourceLimit).map((source, index) => <details className="generation-context" key={index}><summary>{String(source.metadata?.document_name || source.metadata?.filename || `${t('来源片段')} ${index + 1}`)}{typeof source.metadata?.knowledge_name === 'string' && <span> · {source.metadata.knowledge_name}</span>}</summary><pre>{source.content ?? JSON.stringify({input: source.golden?.input, expected_output: source.golden?.expected_output, context: source.golden?.context}, null, 2)}</pre></details>)}{trace.sources.length > sourceLimit && <button type="button" className="ghost compact" onClick={() => setSourceLimit(value => value + 20)}>{t('加载更多来源')}</button>}</> : <p className="field-help">{t('此生成方式未使用来源资料。')}</p>}</section>}
        </div>}
      </>}
    </div>
    <div className="workspace-dialog-footer"><button type="button" className="ghost" onClick={onClose}>{t('关闭')}</button></div>
  </WorkspaceDialog>;
}

function TraceRequests({datasetId, itemId, language}: {datasetId: string; itemId: string; language: Language}) {
  const t = (text: string) => tr(language, text);
  const [page, setPage] = useState(1), [size, setSize] = useState(10);
  const [result, setResult] = useState<PageResult<GenerationRequestSummary> | null>(null);
  const [error, setError] = useState(''), [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResult(null); setError('');
    datasetApi.generationRequests(datasetId, itemId, page, size, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setResult(value); if (value.page !== page) setPage(value.page);
    }).catch(reason => {if (!controller.signal.aborted) setError(errorText(reason));});
    return () => controller.abort();
  }, [datasetId, itemId, page, size, reload]);
  return <div className="generation-requests">
    {error ? <div role="alert"><p className="error-text">{error}</p><button className="ghost" onClick={() => setReload(value => value + 1)}>{t('重试')}</button></div> : !result ? <p role="status">{t('加载中…')}</p> : <>
      {!result.total && <p className="field-help">{t('没有记录到模型请求。')}</p>}
      {result.items.map(request => <TraceRequest key={request.id} datasetId={datasetId} itemId={itemId} request={request} language={language} />)}
      {result.total > 0 && <Pagination total={result.total} page={page} pageSize={size} onPageChange={setPage} onPageSizeChange={value => {setSize(value);setPage(1);}} language={language} label={t('模型请求')} />}
    </>}
  </div>;
}

function TraceRequest({datasetId, itemId, request, language}: {datasetId: string; itemId: string; request: GenerationRequestSummary; language: Language}) {
  const t = (text: string) => tr(language, text);
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<GenerationRequest | null>(null);
  const [error, setError] = useState(''), [reload, setReload] = useState(0);
  useEffect(() => {
    if (!open || data) return;
    const controller = new AbortController();
    setError('');
    datasetApi.generationRequest(datasetId, itemId, request.id, controller.signal).then(value => {
      if (!controller.signal.aborted) setData(value);
    }).catch(reason => {if (!controller.signal.aborted) setError(errorText(reason));});
    return () => controller.abort();
  }, [open, data, datasetId, itemId, request.id, reload]);
  return <article className="generation-request">
    <button className="generation-request-toggle" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}<span>#{request.sequence} · {t(request.role === 'quality_review' ? '质量评审' : '生成 / 改写')} · {request.model}</span><time dateTime={request.created_at}>{new Date(request.created_at).toLocaleTimeString(language)}</time></button>
    {open && <div className="generation-request-content">{error ? <div role="alert"><p className="error-text">{error}</p><button className="ghost" onClick={() => setReload(value => value + 1)}>{t('重试')}</button></div> : !data ? <p role="status">{t('加载中…')}</p> : <>{data.request.messages.map((message, index) => <section className="generation-message" key={index}><h4>{message.role === 'system' ? t('系统消息') : message.role === 'user' ? t('用户消息') : message.role}</h4><pre>{message.content}</pre></section>)}<details><summary>{t('请求参数')}</summary><pre>{JSON.stringify(Object.fromEntries(Object.entries(data.request).filter(([key]) => key !== 'messages')), null, 2)}</pre></details></>}</div>}
  </article>;
}
