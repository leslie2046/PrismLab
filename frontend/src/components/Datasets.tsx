import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Archive, Activity, CheckCircle2, ArrowLeft, Database, Download, FileUp, Pencil, Plus, RefreshCw, Search, Settings2, Sparkles, Trash2 } from 'lucide-react';
import type { Catalog, Dataset, DatasetItem, DatasetSettings, DatasetSources, LlmSelection } from '../types';
import type { PageResult } from '../pagination';
import { datasetApi, downloadBlob, embeddingChoices, llmChoices, selectionValue } from '../datasetApi';
import { tr, type Language } from '../i18n';
import { GenerationDetails, type GenerationTab } from './GenerationDetails';
import { PageHeader } from './PageHeader';
import { EmptyState } from './EmptyState';
import { Evaluations } from './Evaluations';
import { CompactSwitch } from './CompactSwitch';
import { Pagination } from './Pagination';
import { WorkspaceDialog } from './WorkspaceDialog';
import { SearchableSelect } from './SearchableSelect';
import { SearchableMultiSelect } from './SearchableMultiSelect';
import { InfoTip } from './InfoTip';
import { EvolutionSettings, validEvolutionSettings } from './EvolutionSettings';
import { useBulkSelection } from '../bulkSelection';
import { BulkSelectionBar, SelectionCheckbox } from './BulkSelection';
import {datasetPurposeLabels, type DatasetPurpose} from '../evaluationTypes';
import {validRagasDistribution} from '../ragasDistribution';
import { RagasDistributionSettings } from './RagasDistributionSettings';
import { EvaluationStatus } from './EvaluationStatus';
import { metricNames } from './EvaluationDialog';
import '../datasets.css';

type Mode = 'import' | 'documents' | 'dify' | 'manual';
const createModeLabels = { import: '数据集导入', documents: '从文档生成', dify: '从Dify知识库生成', manual: '空白数据集', goldens: '种子扩写', scratch: '按任务生成' };
const statusLabels = { ready: '可用', generating: '构建中', failed: '构建失败', cancelled: '已取消' };
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const renderValue = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? '';
const emptyPage = <T,>(): PageResult<T> => ({ items: [], total: 0, page: 1, page_size: 10 });

export function Datasets({ catalog, language, initialDifyConfigId, onCreateClosed, onDatasetChange }: { catalog: Catalog | null; language: Language; initialDifyConfigId?: string | null; onCreateClosed?: () => void; onDatasetChange?: (name: string | null) => void }) {
  const t = (value: string) => tr(language, value);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [list, setList] = useState(emptyPage<Dataset>);
  const [items, setItems] = useState(emptyPage<DatasetItem>);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [purpose, setPurpose] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [progressError, setProgressError] = useState('');
  const [reload, setReload] = useState(0);
  const [create, setCreate] = useState(!!initialDifyConfigId);
  const [generationView, setGenerationView] = useState<{item: DatasetItem; tab: GenerationTab} | null>(null);
  const [editing, setEditing] = useState<DatasetItem | 'new' | null>(null);
  const [detailTab, setDetailTab] = useState<'samples' | 'evaluations' | 'settings'>('samples');
  const [saved, setSaved] = useState('');
  const [syncingSource, setSyncingSource] = useState<string | null>(null);
  const [sourceMessage, setSourceMessage] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [exportIds, setExportIds] = useState<string[] | null>(null);
  const [append, setAppend] = useState(false);
  const [maintenance, setMaintenance] = useState<'config' | 'documents' | 'dify' | 'build' | null>(null);
  const [sources, setSources] = useState<DatasetSources | null>(null);
  const id = dataset?.id;
  const generating = dataset?.status === 'generating';
  const retrievalOnly = dataset?.evaluation_purpose === 'retrieval';
  const knowledgeSourceCount = new Set([
    ...(dataset?.dify_config_ids || []), ...(dataset?.dify_config_id ? [dataset.dify_config_id] : []),
    ...(sources?.items || dataset?.data_sources || []).filter(source => source.kind === 'dify')
      .map(source => source.config_id || source.dataset_id || source.name),
  ]).size;
  const selection = useBulkSelection(JSON.stringify([id, q]));
  const selectionDisabled = busy || generating || loading || !!error || q !== query;
  const pageIds = items.items.map(item => item.id);
  const pageChecked = pageIds.length > 0 && pageIds.every(id => selection.ids.has(id));
  const pageMixed = !pageChecked && pageIds.some(id => selection.ids.has(id));
  useEffect(() => { const timer = setTimeout(() => { setQuery(q); setPage(1); }, 250); return () => clearTimeout(timer); }, [q]);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true); setError('');
    const work = id ? Promise.all([datasetApi.get(id, controller.signal), datasetApi.items(id, page, size, query, controller.signal), datasetApi.sources(id, controller.signal)]) : datasetApi.list(page, size, query, controller.signal, purpose);
    work.then(value => {
      if (!active) return;
      if (Array.isArray(value)) { setDataset(value[0]); setItems(value[1]); setSources(value[2]); if (value[1].page !== page) setPage(value[1].page); }
      else { setList(value); if (value.page !== page) setPage(value.page); }
    }).catch(reason => { if (active && reason.name !== 'AbortError') setError(message(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [id, page, size, query, reload, purpose]);
  useEffect(() => {
    if (!id || !generating) return;
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await datasetApi.get(id!, controller.signal);
        if (!active) return;
        // A cancellation response may arrive before an older status response.
        setDataset(current => current && current.id === id && current.status === 'generating' ? value : current);
        setProgressError('');
        if (value.status !== 'generating') {
          setReload(value => value + 1);
          return;
        }
      } catch (reason) {
        if (!active) return;
        setProgressError(message(reason));
      }
      if (active) timer = setTimeout(() => void poll(), 2000);
    }
    setProgressError('');
    timer = setTimeout(() => void poll(), 1000);
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [id, generating]);
  const listGenerating = !id && list.items.some(value => value.status === 'generating');
  useEffect(() => {
    if (!listGenerating) return;
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await datasetApi.list(page, size, query, controller.signal, purpose);
        if (!active) return;
        setList(value);
        if (!value.items.some(item => item.status === 'generating')) return;
      } catch { if (!active) return; }
      timer = setTimeout(() => void poll(), 2000);
    }
    timer = setTimeout(() => void poll(), 2000);
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [listGenerating, page, size, query, purpose]);
  useEffect(() => { onDatasetChange?.(dataset?.name || null); }, [dataset?.name, onDatasetChange]);
  function open(value: Dataset | null) { setSourceMessage(''); setSourceError(''); setDetailTab('samples'); setSaved(''); setSources(null); setDataset(value); setItems(emptyPage()); setPage(1); setQ(''); setQuery(''); setError(''); }
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); setReload(value => value + 1); }
    catch (reason) { setError(message(reason)); }
    finally { setBusy(false); }
  }
  async function syncKnowledge(configId: string, name: string) {
    if (!dataset) return;
    setBusy(true); setSyncingSource(configId); setSourceMessage(''); setSourceError('');
    try {
      const form = new FormData(); form.append('config_ids', configId);
      setDataset(await datasetApi.syncDify(dataset.id, form));
      setReload(value => value + 1);
      setSourceMessage(`${name} · ${t('同步完成')}`);
    } catch (reason) { setSourceError(message(reason)); }
    finally { setBusy(false); setSyncingSource(null); }
  }
  function bulkItems(action: 'enable' | 'archive' | 'delete') {
    if (!dataset) return;
    if (action === 'delete' && !window.confirm(`${t('批量删除')} ${selection.ids.size} ${t('条记录')}？ ${t('此操作无法撤销。')}`)) return;
    void selection.execute(ids => datasetApi.bulkItems(dataset.id, action, ids), () => setReload(value => value + 1));
  }
  function regenerateAnswers(ids: string[]) {
    if (!dataset || !ids.length || generating || busy) return;
    if (!window.confirm(`${t('确认重新生成')} ${ids.length} ${t('条样本的参考答案？')} ${t('问题、上下文和来源不会改变。')}`)) return;
    void act(async () => { const value = await datasetApi.regenerateAnswers(dataset.id, ids); setDataset(value); selection.clear(); });
  }
  const data = id ? items : list;
  return <section className="panel datasets-panel">
    <PageHeader className={`dataset-heading ${dataset ? 'dataset-detail-heading' : ''}`} icon={Database} title={dataset ? dataset.name : t('评测数据集')} count={!dataset ? list.total : undefined} description={!dataset ? t('管理问题、参考答案与检索标注，可用于 RAG 或检索评测。') : undefined} meta={dataset && <div className="dataset-heading-meta"><DatasetStatus dataset={dataset} language={language} /><span>{t(datasetPurposeLabels[dataset.evaluation_purpose || 'both'])}</span><span>{knowledgeSourceCount} {t('个 Dify 知识库')}</span>{dataset.source_chunk_count !== undefined && <span>{dataset.source_chunk_count} {t('个知识片段')}</span>}</div>} breadcrumb={dataset && <button type="button" className="dataset-back ghost" onClick={() => open(null)}><ArrowLeft />{t('所有评测数据集')}</button>} actions={<div className="dataset-actions">{dataset ? <>
      <button type="button" className="ghost" disabled={busy || generating || selection.busy} onClick={() => setMaintenance('build')}><Sparkles aria-hidden="true" />{t('重新构建')}</button>
      {detailTab === 'samples' && <button type="button" className="ghost" disabled={busy || generating || loading || !!error || q !== query || selection.busy || !selection.ids.size} onClick={() => regenerateAnswers([...selection.ids])}><Sparkles aria-hidden="true" />{t('重新生成答案')}</button>}
      {detailTab === 'samples' && <>
      <button type="button" className="ghost" disabled={busy || generating || selection.busy} onClick={() => setMaintenance('dify')}><FileUp aria-hidden="true" />{t('导入知识库来源')}</button>
      <button type="button" className="ghost" disabled={busy || loading || !!error || selection.busy || q !== query} onClick={() => setExportIds([...selection.ids])}><Download aria-hidden="true" />{t('批量导出')}</button>

      {!retrievalOnly && <button type="button" className="ghost" disabled={busy || generating || selection.busy} onClick={() => setAppend(true)}><FileUp aria-hidden="true" />{t('追加导入')}</button>}
      {!retrievalOnly && <button type="button" className="primary" disabled={busy || generating || selection.busy} onClick={() => setEditing('new')}><Plus aria-hidden="true" />{t('添加数据项')}</button>}
    </>}
    </> : <button type="button" className="primary compact" onClick={() => setCreate(true)}><Plus aria-hidden="true" />{t('新建评测数据集')}</button>}</div>} />
    {dataset && (generating || dataset.status === 'cancelled') && <BuildProgress dataset={dataset} language={language} busy={busy} error={progressError} onCancel={() => void act(async () => setDataset(await datasetApi.cancel(dataset.id)))} onRetry={() => void act(async () => setDataset(await datasetApi.retry(dataset.id)))} />}
    {dataset && <div className="dataset-local-tabs" role="tablist" aria-label={t('数据集导航')} onKeyDown={event => {if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const tabs = ['samples','evaluations','settings'] as const;const index = tabs.indexOf(detailTab);const next = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3];setDetailTab(next);document.getElementById(`dataset-${next}-tab`)?.focus();}}}>
      <button role="tab" tabIndex={detailTab === 'samples' ? 0 : -1} id="dataset-samples-tab" aria-controls="dataset-samples" aria-selected={detailTab === 'samples'} onClick={() => setDetailTab('samples')}><Database aria-hidden="true" />{t('样本')}<span>{dataset.item_count}</span></button>
      <button role="tab" tabIndex={detailTab === 'evaluations' ? 0 : -1} id="dataset-evaluations-tab" aria-controls="dataset-evaluations" aria-selected={detailTab === 'evaluations'} onClick={() => setDetailTab('evaluations')}><Activity aria-hidden="true" />{t('评测记录')}</button>
      <button role="tab" tabIndex={detailTab === 'settings' ? 0 : -1} id="dataset-settings-tab" aria-controls="dataset-settings" aria-selected={detailTab === 'settings'} onClick={() => setDetailTab('settings')}><Settings2 aria-hidden="true" />{t('设置')}</button>
    </div>}
    {dataset && detailTab === 'evaluations' && <div id="dataset-evaluations" role="tabpanel" aria-labelledby="dataset-evaluations-tab"><Evaluations key={dataset.id} catalog={catalog} language={language} dataset={dataset} /></div>}
    {dataset && <div hidden={detailTab !== 'settings'} id="dataset-settings" role="tabpanel" aria-labelledby="dataset-settings-tab" className="dataset-settings-page">
      {saved && <p className="dataset-save-success" role="status"><CheckCircle2 aria-hidden="true" />{saved}</p>}
      <section className="dataset-settings-section"><header><h3>{t('基本信息')}</h3><p>{t('名称与评测用途')}</p></header><DatasetMetadata key={dataset.id} embedded dataset={dataset} language={language} onClose={() => {}} onSaved={value => {setDataset(value);setSaved(t('基本信息已保存'));}} /></section>
      <section className="dataset-settings-section"><header><h3>{t('生成配置')}</h3><p>{t('配置模型和生成规则，保存后在下次构建时生效。')}</p></header><DatasetMaintenance key={`${dataset.id}-${dataset.evaluation_purpose}`} embedded mode="config" dataset={dataset} sources={sources} catalog={catalog} language={language} onClose={() => {}} onSaved={value => {setDataset(value);setSaved(t('生成配置已保存'));}} /></section>
      <section className="dataset-settings-section"><header><h3>{t('来源资料')}</h3><p>{t(retrievalOnly ? '同步知识库最新片段，供后续生成样本使用。' : '管理用于生成样本的文档和知识库。')}</p></header><div className="dataset-source-content"><div className="dataset-actions">{!retrievalOnly && <button className="ghost" disabled={busy || generating} onClick={() => setMaintenance('documents')}><FileUp />{t('导入文档')}</button>}<button className="ghost" disabled={busy || generating} onClick={() => setMaintenance('dify')}><RefreshCw />{t('导入知识库来源')}</button></div>{sourceMessage && <p className="dataset-save-success" role="status"><CheckCircle2 aria-hidden="true" />{sourceMessage}</p>}{sourceError && <p className="error-text" role="alert">{sourceError}</p>}{sources?.items.length ? <ul className="dataset-source-list">{sources.items.map((source,index) => <li key={source.config_id || index}><div className="dataset-source-identity"><span>{source.name}</span>{source.kind === 'dify' && <button type="button" className="ghost compact dataset-source-sync" aria-label={`${t('同步知识库')} ${source.name}`} disabled={busy || generating || !source.config_id} onClick={() => source.config_id && void syncKnowledge(source.config_id, source.name)}><RefreshCw aria-hidden="true" className={syncingSource === source.config_id ? 'spin' : undefined} />{t(syncingSource === source.config_id ? '同步中…' : '同步')}</button>}</div><small>{source.kind === 'dify' ? 'Dify' : t('文档')} · {source.chunk_count} {t('个片段')}</small></li>)}</ul> : <p className="field-help">{t('尚未同步 Dify 知识库。')}</p>}</div></section>

    </div>}
    {<div hidden={!!dataset && detailTab !== 'samples'} id="dataset-samples" role={dataset ? 'tabpanel' : undefined} aria-labelledby={dataset ? 'dataset-samples-tab' : undefined}>
    {dataset && <div className="dataset-sample-tools"><span>{t('样本管理')}</span></div>}
    {dataset?.error && <div className="dataset-feedback warning" role="alert"><span>{dataset.error}</span>{dataset.status === 'failed' && <button type="button" className="ghost" disabled={busy} onClick={() => void act(async () => setDataset(await datasetApi.retry(dataset.id)))}><RefreshCw aria-hidden="true" />{t('继续构建')}</button>}</div>}

    <div className="dataset-toolbar"><label className="dataset-search"><Search aria-hidden="true" /><input aria-label={t(id ? '搜索数据项' : '搜索数据集')} placeholder={t(id ? '搜索数据项' : '搜索数据集')} value={q} maxLength={200} onChange={event => setQ(event.target.value)} /></label>{!dataset && <label className="field"><select aria-label={t('筛选数据集用途')} value={purpose} onChange={e => {setPurpose(e.target.value);setPage(1);}}><option value="">{t('全部用途')}</option>{Object.entries(datasetPurposeLabels).map(([value,label])=><option key={value} value={value}>{t(label)}</option>)}</select></label>}<button type="button" className="ghost" disabled={loading} onClick={() => setReload(value => value + 1)}><RefreshCw aria-hidden="true" />{t('刷新')}</button></div>
    {dataset && <BulkSelectionBar selection={selection} language={language}>
      <button className="ghost" disabled={selectionDisabled || selection.busy} onClick={() => void selection.selectAll(() => datasetApi.itemSelection(dataset.id, query))}>{t('全选全部匹配记录')}</button>
      <button className="ghost" disabled={selection.busy} onClick={selection.clear}>{t('全不选')}</button>
      <button type="button" className="ghost" disabled={selectionDisabled || selection.busy} onClick={() => setExportIds([...selection.ids])}><Download aria-hidden="true" />{t('批量导出')}</button>
      <button type="button" className="ghost" disabled={selectionDisabled || selection.busy} onClick={() => bulkItems('enable')}><CheckCircle2 aria-hidden="true" />{t('批量启用')}</button>
      <button type="button" className="ghost" disabled={selectionDisabled || selection.busy} onClick={() => bulkItems('archive')}><Archive aria-hidden="true" />{t('批量归档')}</button>
      <button type="button" className="ghost danger" disabled={selectionDisabled || selection.busy} onClick={() => bulkItems('delete')}><Trash2 aria-hidden="true" />{t('批量删除')}</button>
    </BulkSelectionBar>}
    {error && <div className="dataset-feedback error" role="alert">{error}<button type="button" className="ghost" onClick={() => setReload(value => value + 1)}>{t('重试')}</button></div>}
    {loading && <p className="pagination-feedback" role="status">{t('加载中…')}</p>}
    {dataset ? <div className="dataset-sample-list" aria-busy={loading}>
      <div className="dataset-sample-list-head"><SelectionCheckbox label={t('全选当前页')} checked={pageChecked} mixed={pageMixed} disabled={selectionDisabled || selection.busy || !pageIds.length} onChange={checked => selection.toggle(pageIds, checked)} /><span>{t('输入 / 问题')} · {t('参考答案')}</span><span>{t('状态')}</span></div>
      {items.items.map((item,index) => <article key={item.id} className={`dataset-sample-row ${selection.ids.has(item.id) ? 'bulk-selected' : ''}`}>
        <SelectionCheckbox label={`${t('选择记录')} ${item.id}`} checked={selection.ids.has(item.id)} disabled={selectionDisabled || selection.busy} onChange={checked => selection.toggle([item.id], checked)} />
        <div className="dataset-sample-main"><button className="dataset-sample-content" disabled={selection.busy} onClick={() => setEditing(item)}><small>#{(page-1)*size+index+1} · {item.context?.length || 0} {t('个片段')}</small><div><b>Q</b><strong>{renderValue(item.input)}</strong></div><div><b>A</b><span>{renderValue(item.expected_output) || t('未设置参考答案')}</span></div></button><div className="dataset-sample-trace-links"><button type="button" onClick={() => setGenerationView({item, tab: 'prompts'})}>{t('生成提示词')}</button><button type="button" onClick={() => setGenerationView({item, tab: 'context'})}>{t('上下文与来源')} ({item.context?.length || 0})</button></div></div>
        <div className="dataset-sample-operations"><CompactSwitch title={t(item.status === 'active' ? '归档数据项' : '启用数据项')} checked={item.status === 'active'} disabled={selectionDisabled || selection.busy} label={t(item.status === 'active' ? '归档数据项' : '启用数据项')} onChange={() => void act(async () => {const result = await datasetApi.bulkItems(dataset.id,item.status === 'active' ? 'archive' : 'enable',[item.id]);if(result.failed.length) throw new Error(result.failed[0].message);})} /><span>{t(item.status === 'active' ? '启用' : '归档')}</span><button className="icon-button" aria-label={t('重新生成答案')} title={t('重新生成答案')} disabled={selectionDisabled || selection.busy || !item.context?.length} onClick={() => regenerateAnswers([item.id])}><Sparkles aria-hidden="true" /></button>{!retrievalOnly && <button className="icon-button" aria-label={t('查看或编辑数据项')} disabled={selection.busy} onClick={() => setEditing(item)}><Pencil /></button>}<button className="icon-button" aria-label={t('删除数据项')} disabled={busy || generating || selection.busy} onClick={() => {if(window.confirm(t('确认删除这条数据项？'))) void act(() => datasetApi.deleteItem(dataset.id,item.id));}}><Trash2 /></button></div>
      </article>)}
    </div> : <div className="dataset-table-wrap" tabIndex={0} role="region" aria-label={t('评测数据集列表')} aria-busy={loading}>
      <table className="dataset-table dataset-index-table">
        <thead><tr>
          {['名称', '数据集用途', '数据来源', '样本数', '状态'].map(label => <th key={label} scope="col">{t(label)}</th>)}
          <th scope="col"><span className="label-with-tip">{t('最新评测 · 指标均分')}<InfoTip label={t('最新评测摘要说明')} text={t('显示最近创建的一次评测，各指标分别取有效评分样本的平均分。分数范围为 0–1，越高越好；缺少数据、执行失败和等待中的样本不记为零分。')} /></span></th>
          <th scope="col">{t('更新时间')}</th><th scope="col">{t('操作')}</th>
        </tr></thead>
        <tbody>{list.items.map(value => <tr key={value.id}>
          <th scope="row"><button type="button" className="dataset-cell-button dataset-name-cell" onClick={() => open(value)}><strong>{value.name}</strong>{value.description && <small>{value.description}</small>}</button></th>
          <td>{t(datasetPurposeLabels[value.evaluation_purpose || 'both'])}</td>
          <td><div className="dataset-source-cell">{value.data_sources?.length ? value.data_sources.map((source,index) => <span key={index} title={source.name}>{source.kind === 'dify' ? source.name : source.kind === 'documents' || source.kind === 'import' ? source.name : t(({manual:'手动创建',goldens:'种子扩写',scratch:'按任务生成'} as Record<string,string>)[source.kind] || source.name)}</span>) : <span className="muted">{t('暂无来源')}</span>}</div></td>
          <td className="dataset-index-count">{value.item_count}</td>
          <td><DatasetStatus dataset={value} language={language} /></td>
          <td><LatestDatasetEvaluation dataset={value} language={language} /></td>
          <td><time dateTime={value.updated_at}>{new Date(value.updated_at).toLocaleDateString(language)}</time></td>
          <td><button type="button" className="icon-button" aria-label={`${t('删除数据集')} ${value.name}`} disabled={busy || value.status === 'generating'} onClick={() => {if(window.confirm(`${t('确认删除数据集及其所有数据项？')} ${value.name}`)) void act(() => datasetApi.delete(value.id));}}><Trash2 aria-hidden="true" /></button></td>
        </tr>)}</tbody>
      </table>
    </div>}


    {!loading && !error && !data.items.length && <EmptyState icon={Database} title={t(q ? '没有匹配的数据，请调整搜索条件。' : id ? '暂无数据项，可手动添加或导入数据集。' : '暂无数据集，导入已有数据或让 AI 从知识内容生成。')} />}
    <Pagination page={data.page} pageSize={size} total={data.total} language={language} label={t(id ? '数据项' : '数据集')} onPageChange={setPage} onPageSizeChange={value => { setSize(value); setPage(1); }} disabled={loading || selection.busy} />
    </div>}
    {create && <CreateDataset catalog={catalog} language={language} initialDifyConfigId={initialDifyConfigId} onClose={() => { setCreate(false); onCreateClosed?.(); }} onCreated={value => { setCreate(false); onCreateClosed?.(); open(value); setReload(n => n + 1); }} />}
    {generationView && dataset && <GenerationDetails datasetId={dataset.id} item={generationView.item} initialTab={generationView.tab} language={language} onClose={() => setGenerationView(null)} />}
    {editing && dataset && <ItemEditor item={editing === 'new' ? null : editing} dataset={dataset} language={language} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setReload(value => value + 1); }} />}

    {maintenance && dataset && <DatasetMaintenance mode={maintenance} dataset={dataset} sources={sources} catalog={catalog} language={language} onClose={() => setMaintenance(null)} onSaved={value => { setDataset(value); setMaintenance(null); setReload(n => n + 1); }} />}
    {append && dataset && <ImportItems dataset={dataset} language={language} onClose={() => setAppend(false)} onSaved={() => { setAppend(false); setReload(value => value + 1); }} />}
    {exportIds && dataset && <ExportDataset dataset={dataset} ids={exportIds} language={language} onClose={() => setExportIds(null)} />}
  </section>;
}

function ExportDataset({ dataset, ids, language, onClose }: { dataset: Dataset; ids: string[]; language: Language; onClose: () => void }) {
  const t = (value: string) => tr(language, value);
  const [scope, setScope] = useState(ids.length ? 'selected' : 'all');
  const [format, setFormat] = useState<'json' | 'jsonl' | 'csv'>('json');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const count = scope === 'selected' ? ids.length : dataset.item_count;
  return <WorkspaceDialog title={t('批量导出')} language={language} busy={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault();
    if (busy || !count) return;
    setBusy(true); setError('');
    try {
      const blob = await datasetApi.export(dataset.id, format, scope === 'selected' ? ids : undefined);
      downloadBlob(blob, `dataset-${dataset.id}.${format}`);
      onClose();
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(false); }
  }}>
    <div className="workspace-dialog-body">
      <label className="field"><span>{t('导出范围')}</span><select aria-label={t('导出范围')} value={scope} disabled={busy} onChange={event => setScope(event.target.value)}>
        <option value="selected" disabled={!ids.length}>{t('已勾选数据项')} ({ids.length})</option>
        <option value="all">{t('全部数据项')} ({dataset.item_count})</option>
      </select></label>
      <label className="field"><span>{t('导出格式')}</span><select aria-label={t('导出格式')} value={format} disabled={busy} onChange={event => setFormat(event.target.value as typeof format)}>{['json', 'jsonl', 'csv'].map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}</select></label>
      <p className="field-help">{t(scope === 'selected' ? '导出已勾选的数据项，包含跨页选择的记录。' : '导出整个数据集，包含启用和归档的数据项，不受当前搜索条件影响。')}</p>
      {!count && <p className="field-help" role="status">{t('暂无可导出的数据项。')}</p>}
      {busy && <p className="field-help" role="status">{t('正在准备导出文件…')}</p>}
      {error && <p className="error-text" role="alert">{error}</p>}
    </div>
    <div className="workspace-dialog-footer"><button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('取消')}</button><button type="submit" className="primary" disabled={busy || !count}><Download aria-hidden="true" />{t(busy ? '导出中…' : '确认导出')} ({count})</button></div>
  </form></WorkspaceDialog>;
}

function BuildProgress({dataset, language, busy, error, onCancel, onRetry}: {dataset: Dataset; language: Language; busy: boolean; error: string; onCancel: () => void; onRetry: () => void}) {
  const t = (value: string) => tr(language, value);
  const [now, setNow] = useState(Date.now);
  const generating = dataset.status === 'generating';
  useEffect(() => {
    if (!generating) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [generating]);
  const target = dataset.build_target ?? dataset.target_size ?? 0;
  const completed = dataset.build_progress ?? dataset.item_count;
  const waiting = dataset.build_active_requests ? Math.max(0, Math.floor((now - Date.parse(dataset.build_request_started_at || dataset.updated_at)) / 1000)) : 0;
  return <div className="dataset-feedback dataset-build-progress">
    <Sparkles aria-hidden="true" />
    <div className="dataset-build-details">
      <div className="dataset-build-numbers" role="status" aria-live="polite"><strong>{t(generating ? '已完成' : '已取消')} {completed} / {target}</strong><span>{t('条样本')}</span><span>{t('模型请求')} {dataset.build_requests ?? 0} {t('次')}</span><span>{t('连续失败重试')} {dataset.build_retry_count ?? 0} / {dataset.build_retry_limit ?? 2}</span></div>
      <progress aria-label={t('构建进度')} value={completed} max={Math.max(1, target)} />
      <small>{t(!generating ? dataset.build_mode === 'regenerate_answers' ? '答案重新生成已停止，已完成的新答案保留。' : dataset.build_mode === 'replace_generated' ? '已停止生成，原有数据已保留。重新开始会重新生成整批样本。' : '已停止生成，已保存的样本保留。' : dataset.build_mode === 'regenerate_answers' ? '只更新参考答案，问题、上下文和来源保持不变。每 2 秒自动更新；连续失败最多重试 2 次。' : dataset.build_stage === 'sources' ? '正在读取来源资料…' : '每 2 秒自动更新；连续失败最多重试 2 次。')}{generating && !!dataset.build_active_requests && <> · {t('进行中')} {dataset.build_active_requests} · {t('当前请求已等待')} {waiting} {t('秒')}</>}</small>
      {dataset.build_last_error && <small className="error-text">{dataset.build_last_error}</small>}
      {generating && error && <small className="error-text" role="alert">{t('进度更新失败，正在重新连接…')}</small>}
    </div>
    <button type="button" className="ghost" disabled={busy} onClick={generating ? onCancel : onRetry}>{t(busy ? '提交中…' : generating ? '取消生成' : '继续构建')}</button>
  </div>;
}

function DatasetStatus({ dataset, language }: { dataset: Dataset; language: Language }) {
  return <span className={`status status-${dataset.status === 'ready' ? 'completed' : dataset.status === 'generating' ? 'running' : dataset.status === 'cancelled' ? 'cancelled' : 'failed'}`}><i />{tr(language, statusLabels[dataset.status])}{dataset.status === 'generating' && ` ${dataset.build_progress ?? 0} / ${dataset.build_target ?? dataset.target_size ?? 0}`}</span>;
}

function LatestDatasetEvaluation({ dataset, language }: { dataset: Dataset; language: Language }) {
  const t = (value: string) => tr(language, value);
  const run = dataset.latest_evaluation;
  if (!run) return <span className="dataset-evaluation-empty">{t('暂无评测')}</span>;
  return <div className="dataset-evaluation-summary">
    <div className="dataset-evaluation-meta"><EvaluationStatus status={run.status} language={language} /><span title={run.name}>{run.name}</span><time dateTime={run.created_at}>{new Date(run.created_at).toLocaleDateString(language)}</time></div>
    {Object.keys(run.metrics).length ? <dl className="dataset-evaluation-metrics">{Object.entries(run.metrics).map(([id, metric]) => <div key={id} title={`${id} · ${t('有效评分')} ${metric.scored} / ${run.case_count}`}>
      <dt>{t(metricNames[id] || id)}</dt><dd>{typeof metric.mean === 'number' && Number.isFinite(metric.mean) ? metric.mean.toFixed(3) : '—'}</dd>
    </div>)}</dl> : <span className="dataset-evaluation-empty">{t('本次评测未配置评分指标。')}</span>}
    {!Object.values(run.metrics).some(metric => typeof metric.mean === 'number' && Number.isFinite(metric.mean)) && Object.keys(run.metrics).length > 0 && <span className="dataset-evaluation-empty">{t('暂无有效评分')}</span>}
  </div>;
}

function ImportHelp({ language }: { language: Language }) {
  const t = (value: string) => tr(language, value);
  return <div className="dataset-import-help"><p>{t('支持 DeepEval Golden 的 CSV、JSON、JSONL，最多 10 MB / 20000 条，导出可直接加载为 Golden。')}</p><p>{t('标准字段为 input、expected_output、context、additional_metadata；也兼容旧字段，并保留工具调用、标签和检索结果。')}</p><button type="button" className="ghost" onClick={() => downloadBlob(new Blob([JSON.stringify([{ input: 'PrismLab 是什么？', expected_output: 'AI 模型测试与分析平台。', context: ['PrismLab 是 AI 模型测试与分析平台。'], additional_metadata: { source: 'example' } }], null, 2)], { type: 'application/json' }), 'dataset-example.json')}><Download aria-hidden="true" />{t('下载导入示例')}</button></div>;
}

function CreateDataset({ catalog, language, initialDifyConfigId, onClose, onCreated }: { catalog: Catalog | null; language: Language; initialDifyConfigId?: string | null; onClose: () => void; onCreated: (dataset: Dataset) => void }) {
  const t = (value: string) => tr(language, value);
  const [mode, setMode] = useState<Mode>(initialDifyConfigId ? 'dify' : 'import');
  const [name, setName] = useState('');
  const [purpose, setPurpose] = useState<DatasetPurpose>('both');
  const [size, setSize] = useState(50);
  const [ai, setAi] = useState(false);
  const [seed, setSeed] = useState('');
  const [seeds, setSeeds] = useState<Dataset[]>([]);
  const [advanced, setAdvanced] = useState(defaultGenerationOptions);

  const [llm, setLlm] = useState('');
  const [dify, setDify] = useState<string[]>(initialDifyConfigId ? [initialDifyConfigId] : []);
  const [files, setFiles] = useState<File[]>([]);
  const [settings, setSettings] = useState<DatasetSettings | null>(null);
  const [settingsError, setSettingsError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const choices = llmChoices(catalog, language);
  const difyChoices = catalog?.benchmarks.find(value => value.id === 'dify-retrieve')?.providers || [];
  const availableModes: Mode[] = purpose === 'retrieval' ? ['dify'] : ['import', 'documents', 'dify', 'manual'];
  const generate = mode === 'documents' || mode === 'dify' || ai;
  const method = mode === 'import' && ai ? 'goldens' : mode === 'manual' && ai ? 'scratch' : 'contexts';
  const needsFiles = mode === 'documents' || (mode === 'import' && !ai);
  useEffect(() => { if (purpose === 'retrieval' && mode !== 'dify') { setMode('dify'); setAi(false); setFiles([]); } }, [purpose, mode]);
  useEffect(() => {
    if (method !== 'goldens') return;
    let active = true;
    (async () => {
      const first = await datasetApi.list(1, 100, '');
      const values = [...first.items];
      for (let p = 2; p <= Math.ceil(first.total / 100); p++) values.push(...(await datasetApi.list(p, 100, '')).items);
      if (active) setSeeds(values.filter(value => value.status !== 'generating' && value.item_count > 0));
    })().catch(reason => { if (active) setError(message(reason)); });
    return () => { active = false; };
  }, [method]);
  const effectiveEmbedding = advanced.embedding_model || settings?.default_embedding_model;
  const invalidEmbedding = advanced.generation_backend === 'ragas_native' && (!effectiveEmbedding || !embeddingChoices(catalog, language).some(choice => choice.value === selectionValue(effectiveEmbedding)));
  const staleDefault = !!settings?.default_llm && !choices.some(value => value.value === selectionValue(settings.default_llm));
  const staleCritic = advanced.generation_backend === 'deepeval' && !advanced.critic_llm && !!settings?.default_critic_llm && !choices.some(value => value.value === selectionValue(settings.default_critic_llm));
  useEffect(() => {
    let active = true;
    datasetApi.settings().then(value => { if (active) setSettings(value); }).catch(reason => { if (active) setSettingsError(message(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  return <WorkspaceDialog title={t('新建评测数据集')} language={language} busy={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      let value: Dataset;
      if (mode === 'manual' && !ai) value = await datasetApi.create({ name, description: '', evaluation_purpose:purpose });
      else {
        const form = new FormData(); form.set('name', name); form.set('evaluation_purpose', purpose);
        if (mode === 'import' && !ai) { form.set('file', files[0]); value = await datasetApi.import(form); }
        else {
          const generationModel = llm ? JSON.parse(llm) : null;
          const criticModel = advanced.generation_backend !== 'deepeval' ? null : advanced.critic_llm === 'generation' ? generationModel || settings?.default_llm || null : advanced.critic_llm ? JSON.parse(advanced.critic_llm) : null;
          form.set('options', JSON.stringify({ ...advanced, size, method, seed_dataset_id: method === 'goldens' ? seed : null, critic_llm: criticModel, llm: generationModel, dify_config_ids: mode === 'dify' ? dify : [], language }));
          files.forEach(file => form.append('files', file));
          value = await datasetApi.generate(form);
        }
      }
      onCreated(value);
    } catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  }}><div className="workspace-dialog-body dataset-create-body">
    <label className="field"><span>{t('数据集名称')}</span><input autoFocus required maxLength={120} value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label>
    <DatasetPurposeField value={purpose} onChange={next=>{setPurpose(next);setAdvanced(current=>({...current,include_expected_output:next!=='retrieval'}));if(next==='retrieval'){setMode('dify');setAi(false);setFiles([]);}}} language={language} disabled={busy} />
    <div className="dataset-source-tabs" role="group" aria-label={t('创建方式')}>{availableModes.map(value => <button type="button" key={value} aria-pressed={mode === value} disabled={busy} onClick={() => { setMode(value); setFiles([]); setAi(false); setError(''); }}>{t(createModeLabels[value])}</button>)}</div>
    {(mode === 'import' || mode === 'manual') && <label className="dataset-check"><input type="checkbox" checked={ai} disabled={busy} onChange={event => { setAi(event.target.checked); setFiles([]); }} />{t(mode === 'import' ? '使用 AI 扩写已有数据集' : '使用 AI 按任务生成')}</label>}
    {method === 'goldens' && <div className="field"><span className="label-with-tip">{t('种子数据集')}<InfoTip label={t('查看说明')} text={t('使用启用的 Golden 作为种子；没有 context 时仅生成问题。有无上下文的种子需分开。')} /></span><SearchableSelect label={t('种子数据集')} language={language} disabled={busy} value={seed} onChange={setSeed} options={seeds.map(value => ({ value: value.id, label: `${value.name} · ${value.item_count}` }))} /></div>}
    {needsFiles && <label className="field"><span className="label-with-tip">{t(mode === 'import' ? '数据集文件' : '源文档')}<InfoTip label={t('查看说明')} text={t(mode === 'import' ? '导入已有问答和参考上下文，不调用模型。' : '支持 TXT、Markdown、PDF、DOCX、CSV，最多 20 个文件 / 10 MB；扫描 PDF 需先 OCR。')} /></span><input aria-label={t(mode === 'import' ? '数据集文件' : '源文档')} key={mode} type="file" required multiple={mode === 'documents'} accept={mode === 'import' ? '.json,.jsonl,.csv' : '.txt,.md,.markdown,.pdf,.docx,.csv'} disabled={busy} onChange={event => { const next = Array.from(event.target.files || []); setFiles(next); setError(next.reduce((sum, file) => sum + file.size, 0) > 10 * 1024 * 1024 ? t('文件总大小不能超过 10 MB') : ''); }} /></label>}
    {mode === 'import' && !ai && <ImportHelp language={language} />}
    {mode === 'dify' && <div className="field"><span className="label-with-tip">{t('Dify 知识库')}<InfoTip label={t('查看说明')} text={t('使用 Dify 配置中的知识库 ID 和 Dataset API Key，自动分页拉取已启用的文档分段。')} /></span><KnowledgeSelector choices={difyChoices} value={dify} onChange={setDify} language={language} disabled={busy} /></div>}
    {purpose === 'retrieval' && <p className="field-help">{t('检索评测必须使用 Dify 知识库返回的真实分段 ID，因此不支持手动添加、导入文件或从本地文档生成。')}</p>}
    {generate && <><label className="field"><span className="label-with-tip">{t('目标数据集大小')}<InfoTip label={t('查看说明')} text={t('1–500 条问答；有效数据不足时保留已有结果并提示重试。')} /></span><input aria-label={t('目标数据集大小')} required type="number" min={1} max={500} step={1} disabled={busy} value={size} onChange={event => setSize(Number(event.target.value))} /></label>
      <div className="field"><span className="label-with-tip">{t('生成模型')}<InfoTip label={t('查看说明')} text={t('会调用所选 LLM 并产生相应费用。')} /></span><SearchableSelect label={t('生成模型')} language={language} disabled={busy} value={llm} onChange={setLlm} options={[{ value: '', label: t('使用空间默认模型') + (settings?.default_llm ? ` · ${settings.default_llm.model}` : ` · ${t('未配置')}`) }, ...choices]} />{!llm && staleDefault && <small className="error-text">{t('原默认模型已失效，请重新选择')}</small>}</div>
      <GenerationControls value={advanced} onChange={setAdvanced} catalog={catalog} language={language} disabled={busy} scratch={method === 'scratch'} contexts={method === 'contexts'} settings={settings} staleCritic={staleCritic} purpose={purpose} />
      {settingsError && <p className="error-text" role="alert">{settingsError}</p>}
    </>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </div><div className="workspace-dialog-footer"><button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('取消')}</button><button className="primary compact" disabled={busy || !name.trim() || (needsFiles && (!files.length || files.reduce((sum, file) => sum + file.size, 0) > 10 * 1024 * 1024)) || (method === 'goldens' && !seed) || (method === 'scratch' && !advanced.task.trim()) || (mode === 'dify' && !dify.length) || (generate && (loading || staleCritic || invalidEmbedding || !validEvolutionSettings(advanced, method === 'scratch') || !validRagasDistribution(advanced) || (!llm && (!settings?.default_llm || staleDefault))))}>{generate ? <Sparkles aria-hidden="true" /> : <FileUp aria-hidden="true" />}{t(busy ? '提交中…' : generate ? '开始构建' : mode === 'manual' ? '创建' : '导入数据集')}</button></div></form></WorkspaceDialog>;
}

const defaultGenerationOptions = {
  generation_backend: 'deepeval' as 'deepeval' | 'ragas_native',
  embedding_model: null as LlmSelection | null, ragas_query_distribution: {single_hop:1,multi_hop_abstract:0,multi_hop_specific:0} as Record<string,number>, ragas_personas: [] as Array<{name:string;role_description:string}>,
  critic_llm: '', include_expected_output: true, quality_threshold: 0.5, max_quality_retries: 3,
  num_evolutions: 1, context_size: 2,
  evolutions: { CONCRETIZING: 1, CONSTRAINED: 1, COMPARATIVE: 1 } as Record<string, number>,
  task: '', scenario: '', input_format: '', expected_output_format: '',
};
const styleHelp = {
  task: '说明想生成什么内容。例如：“围绕产品退换货政策生成客服问答”。留空时使用空间构建提示词。',
  scenario: '说明谁在什么场景下提问。例如：“电商客服回答售后咨询”。留空使用默认 RAG 评测场景；种子扩写时从种子提取。',
  input_format: '描述问题的语气和形式。例如：“使用口语化中文，每条只问一个问题”。留空时生成独立完整的用户问题；种子扩写时参考种子风格。',
  expected_output_format: '描述参考答案的表达形式。例如：“先给结论，再列出办理步骤”。留空时由模型决定；仅在生成参考答案时生效。',
};
type GenerationStyleKey = keyof typeof styleHelp;
const styleLabels: Record<GenerationStyleKey, string> = { task: '生成任务', scenario: '应用场景', input_format: '问题风格', expected_output_format: '答案风格' };
const styleExamples: Record<GenerationStyleKey | 'scratch', [string, string][]> = {
  "task": [
    [
      "知识库问答",
      "围绕提供的知识资料生成常见问题，覆盖关键概念、适用条件和限制。"
    ],
    [
      "操作流程",
      "根据提供的操作说明生成问答，覆盖办理步骤、所需材料和常见注意事项。"
    ],
    [
      "规则比较",
      "根据资料比较不同方案或规则的适用条件、差异和限制，生成需要比较才能回答的问题。"
    ]
  ],
  "scratch": [
    [
      "售后咨询",
      "为电商售后咨询生成常见问题，覆盖退换货条件、办理流程和进度查询。"
    ],
    [
      "产品使用",
      "生成新用户在使用软件产品时可能提出的问题，覆盖账号注册、功能使用和设置。"
    ],
    [
      "常见故障",
      "生成用户遇到登录失败、连接超时或功能异常时会提出的故障排查问题。"
    ]
  ],
  "scenario": [
    [
      "客户自助",
      "客户通过在线客服咨询产品使用和售后政策，希望获得清楚易懂的解答。"
    ],
    [
      "内部培训",
      "新员工学习内部业务知识，向培训助手咨询规则、流程和注意事项。"
    ],
    [
      "技术支持",
      "技术人员排查系统问题，需要确认操作步骤、适用条件和配置要求。"
    ]
  ],
  "input_format": [
    [
      "自然口语",
      "使用自然、口语化的表达，每条只问一个问题，问题应独立完整。"
    ],
    [
      "正式提问",
      "使用简洁、正式的表达，明确说明提问对象和条件，避免使用“上文”“该内容”等指代。"
    ],
    [
      "场景提问",
      "结合资料中的适用条件描述具体业务场景，再提出一个明确的问题，不添加资料中没有的事实。"
    ]
  ],
  "expected_output_format": [
    [
      "先结论后说明",
      "先直接给出结论，再简要说明依据、适用条件和限制；仅使用资料中的信息。"
    ],
    [
      "分步说明",
      "按编号分步骤说明操作过程，保留资料中的必要条件、材料和注意事项，不编造步骤。"
    ],
    [
      "简短直接",
      "用一到三句话直接回答问题，保留关键数值和限制条件，避免无关扩展。"
    ]
  ]
};
const industryStyleExamples: Array<{ name: string } & Record<GenerationStyleKey, string>> = [
  {
    name: '金融（银行）',
    task: '为银行网点服务机器人生成常见咨询问题，覆盖开户材料、银行卡挂失、业务办理渠道、网点设施和排队叫号。',
    scenario: '客户在银行营业厅向服务机器人咨询业务办理条件、材料和柜台位置，机器人提供业务指引；涉及账户信息或交易时引导至银行官方渠道或工作人员。',
    input_format: '模拟客户在银行营业厅的自然口语提问，例如“银行卡丢了，怎么挂失？”。每条只问一个问题，明确业务和必要条件，不包含真实账号、密码或验证码。',
    expected_output_format: '先说明银行业务能否办理及适用条件，再列出资料中的材料、步骤和办理渠道；不编造收费、利率或办理时限，不索要密码或验证码，无法确认时引导咨询银行工作人员。',
  },
  {
    name: '政务（医保）',
    task: '为医保服务机器人生成常见咨询问题，覆盖参保登记、异地就医备案、报销材料、医保电子凭证和医保窗口办理指引。',
    scenario: '办事群众在政务服务大厅或医保服务窗口向机器人咨询参保、备案和报销，机器人根据资料说明适用地区、参保类型和办理渠道。',
    input_format: '模拟群众咨询医保时的口语提问，例如“去外地住院，医保备案怎么办？”。每条只问一个事项，涉及地区、参保类型或政策年度时仅使用资料中的条件，不填入真实身份信息。',
    expected_output_format: '先说明医保事项的适用地区、参保类型和政策条件，再按步骤列出材料与办理渠道；报销比例、额度和时限仅引用资料，信息不足时说明需向当地医保部门核实。',
  },
  {
    name: '政务（社保）',
    task: '为社保服务机器人生成常见咨询问题，覆盖参保缴费、社保关系转移、缴费记录查询、社保卡申领和养老待遇办理指引。',
    scenario: '办事群众在政务服务大厅向社保机器人咨询跨地区转移、参保缴费和社保卡服务，需要了解适用条件、材料、线上渠道和对应窗口。',
    input_format: '模拟群众咨询社保时的自然提问，例如“换城市工作后，社保怎么转过去？”。每条只问一个事项，必要条件应完整，不编造年龄、缴费年限或当地政策。',
    expected_output_format: '先给出社保事项的办理条件，再分步说明材料、渠道和窗口；缴费标准、年限、待遇金额与时限仅以资料为准，不作个人待遇承诺，无法确认时引导至当地社保部门。',
  },
  {
    name: '医疗（医院）',
    task: '为医院服务机器人生成常见咨询问题，覆盖预约挂号、科室与楼层导引、检查准备、缴费取药、报告领取和便民设施。',
    scenario: '患者或陪诊家属在医院门诊大厅向服务机器人咨询就诊流程、科室位置和检查准备；机器人依据院内资料提供导诊服务，诊疗问题转交医护人员。',
    input_format: '使用患者或家属易懂的口语，例如“预约好了，到了医院先去哪里报到？”。每条只问一个就诊服务问题，不包含真实病历或身份信息，不要求机器人作出诊断。',
    expected_output_format: '先直接回答，再列出资料中的就诊步骤、楼层位置和注意事项；检查准备仅依据院方资料，不提供诊断或自行用药建议，紧急情况提示立即联系现场医护人员。',
  },
  {
    name: '航空（机场候机楼）',
    task: '为机场候机楼服务机器人生成常见咨询问题，覆盖值机托运、安检准备、登机口与中转导引、行李服务、无障碍设施和航班信息查询渠道。',
    scenario: '旅客在机场候机楼向服务机器人咨询出发、到达或中转流程，以及柜台、登机口和便民设施的位置；实时航班变更需通过官方显示屏或航空公司核实。',
    input_format: '模拟旅客在候机楼的简短口语提问，例如“带婴儿坐飞机，在哪里办理值机？”。每条只问一个问题，明确出发、到达或中转场景，不编造航班号、登机口或实时状态。',
    expected_output_format: '先给出办理地点或行动建议，再说明资料中的路线、步骤和限制；保留航站楼与楼层信息，不编造实时登机口、延误情况或安检规定，动态信息引导查看官方渠道。',
  },
  {
    name: '文旅',
    task: '为景区、博物馆等文旅场所的服务机器人生成常见咨询问题，覆盖开放时间、门票预约、游览路线、展陈讲解、无障碍服务和游客设施。',
    scenario: '游客在景区入口、游客中心或文化场馆向机器人咨询预约入场、路线选择、展品背景和便民服务，希望获得易懂且符合现场规则的指引。',
    input_format: '模拟游客的自然口语提问，例如“带老人参观，有不用走楼梯的路线吗？”。每条只问一个问题，说明必要的游览需求，不编造景点、展品或历史事实。',
    expected_output_format: '先回答游客最关心的信息，再简要说明资料中的路线或预约步骤；票价、开放时间、优惠条件和文化史实均以资料为准，临时闭馆或活动变更引导核实场馆公告。',
  },
];
function GenerationStyleField({ field, value, onChange, language, disabled, scratch = false }: {
  field: GenerationStyleKey; value: string; onChange: (value: string) => void;
  language: Language; disabled: boolean; scratch?: boolean;
}) {
  const t = (text: string) => tr(language, text);
  const id = useId();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const label = t(styleLabels[field]);
  const examples = styleExamples[field === 'task' && scratch ? 'scratch' : field];
  const exampleGroups = [
    { label: '点击示例填入', examples },
    { label: '行业服务机器人', examples: industryStyleExamples.map(example => [example.name, example[field]]) },
  ];
  const help = field === 'task' && scratch ? '描述要生成的问题主题，例如：“为电商售后咨询生成常见问题”。无资料生成仅产生问题，不生成参考答案。' : styleHelp[field];
  return <div className="field dataset-style-field">
    <span className="label-with-tip"><label htmlFor={id}>{label}</label><InfoTip label={t('查看说明')} id={`${id}-help`} text={[t(help), t('用自然语言描述要求。点击示例会替换本项内容，填入后仍可编辑。')].join(' ')} /></span>
    <textarea ref={textarea} id={id} aria-label={label} aria-describedby={`${id}-help`} required={field === 'task' && scratch} rows={field === 'task' && scratch ? 3 : 2} maxLength={2000} disabled={disabled} value={value} placeholder={t('例如：') + t(examples[0][1])} onChange={event => onChange(event.target.value)} />
    {exampleGroups.map(group => <div key={group.label} className="dataset-style-examples" role="group" aria-label={`${label} · ${t(group.label)}`}>
      <span>{t(group.label)}</span>
      {group.examples.map(([name, content]) => <button key={name} type="button" disabled={disabled} aria-pressed={value === t(content)} title={t(content)} onClick={() => { onChange(t(content)); textarea.current?.focus(); }}>{t(name)}</button>)}
    </div>)}
  </div>;
}
function GenerationSlider({ label, help, helpId, min, max, step, value, disabled, language, onChange, percent = false }: {
  label: string; help: string; helpId: string; min: number; max: number; step: number;
  value: number; disabled: boolean; language: Language; onChange: (value: number) => void; percent?: boolean;
}) {
  const id = useId();
  return <div className="field">
    <span className="label-with-tip"><label htmlFor={id}>{label}</label><InfoTip label={tr(language, '查看说明')} id={helpId} text={help} /></span>
    <div className="dataset-slider-row"><input id={id} className="dataset-slider" aria-label={label} aria-describedby={helpId} aria-valuetext={percent ? `${value}%` : undefined} type="range" min={min} max={max} step={step} disabled={disabled} value={value} onChange={event => onChange(Number(event.target.value))} /><output htmlFor={id} aria-hidden="true">{value}{percent ? '%' : ''}</output></div>
  </div>;
}
function GenerationControls({ value, onChange, catalog, language, disabled, scratch, contexts, settings, staleCritic, purpose, reviewOutside = false }: { value: typeof defaultGenerationOptions; onChange: (value: typeof defaultGenerationOptions) => void; catalog: Catalog | null; language: Language; disabled: boolean; scratch: boolean; contexts: boolean; settings: DatasetSettings | null; staleCritic: boolean; purpose: DatasetPurpose; reviewOutside?: boolean }) {
  const t = (text: string) => tr(language, text);
  const change = (key: keyof typeof value, next: unknown) => onChange({ ...value, [key]: next });
  const deepEval = value.generation_backend === 'deepeval';
  const embeddings = embeddingChoices(catalog, language);
  const effectiveEmbedding = value.embedding_model || settings?.default_embedding_model;
  const [personaDraft,setPersonaDraft] = useState(value.ragas_personas.map(p=>p.name+' | '+p.role_description).join('\n'));
  const personaId = useId();
  function updatePersonas(text: string) {
    setPersonaDraft(text);
    change('ragas_personas', text.split('\n').filter(line => line.trim()).map(line => {
      const [name, ...rest] = line.split('|');
      return {name: name.trim(), role_description: rest.join('|').trim()};
    }));
  }
  const reviewField = <div className="field"><span className="label-with-tip">{t('质量评审模型')}<InfoTip label={t('查看说明')} text={[t('检查初始问题是否清晰、独立完整且可回答，不代表最终答案正确性。默认使用空间设置；也可单独选择，本次生效。评审会额外调用模型。'), t('仅有参考上下文时进行质量评审；按任务生成和无上下文的种子扩写不进行质量评审。')].join(' ')} /></span><SearchableSelect label={t('质量评审模型')} language={language} disabled={disabled} value={value.critic_llm} onChange={next => change('critic_llm', next)} options={[{ value: '', label: t('使用空间质量评审模型') + ` · ${settings?.default_critic_llm?.model || t('与生成模型相同')}` }, { value: 'generation', label: t('与生成模型相同') }, ...llmChoices(catalog, language)]} />
        {staleCritic && <small className="error-text" role="alert">{t('原质量评审模型已失效，请重新选择')}</small>}
      </div>;
  return <>
    {purpose==='retrieval' && <p className="field-help" role="status">{t('检索评测优先准备相关片段标注，通常无需参考答案。请确保相关片段 ID 与实际检索返回的 ID 对齐；选择需要参考答案的指标时再补充答案。')}</p>}
    <label className="field"><span className="label-with-tip">{t('数据集生成后端')}<InfoTip label={t('查看说明')} text={t('DeepEval 使用官方 Synthesizer；RAGAS 使用 TestsetGenerator，需选择 embedding 模型。')} /></span><select aria-label={t('数据集生成后端')} disabled={disabled} value={(value.generation_backend as string) === 'ragas' ? 'deepeval' : value.generation_backend} onChange={event => change('generation_backend', event.target.value as 'deepeval' | 'ragas_native')}><option value="deepeval">DeepEval</option>{contexts && <option value="ragas_native">RAGAS</option>}</select></label>
    {scratch && <GenerationStyleField field="task" value={value.task} onChange={next => change('task', next)} language={language} disabled={disabled} scratch />}
    {!scratch && <>
      {(['task', 'scenario', 'input_format'] as const).map(key => <GenerationStyleField key={key} field={key} value={value[key]} onChange={next => change(key, next)} language={language} disabled={disabled} />)}
    </>}
    {deepEval && reviewOutside && reviewField}
    <details className="dataset-advanced" open={(!deepEval && (!validRagasDistribution(value) || !effectiveEmbedding)) || (deepEval && staleCritic) || undefined}><summary>{t(value.generation_backend !== 'deepeval' ? 'RAGAS 高级配置' : 'DeepEval 高级配置')}</summary><div className="dataset-advanced-fields">
      {!deepEval && <>
        <div className="field"><span>{t('嵌入模型')}</span><SearchableSelect label={t('嵌入模型')} language={language} disabled={disabled} value={selectionValue(value.embedding_model)} onChange={v=>change('embedding_model',v?JSON.parse(v):null)} options={[{value:'',label:t('使用空间默认嵌入模型')+` · ${settings?.default_embedding_model?.model || t('未配置')}`},...embeddings]}/>
          {(!effectiveEmbedding || !embeddings.some(option=>option.value===selectionValue(effectiveEmbedding))) && <small className="error-text" role="alert">{t(effectiveEmbedding ? '原嵌入模型已失效，请重新选择' : '请选择嵌入模型，或在空间设置中配置默认嵌入模型')}</small>}
        </div>
        <RagasDistributionSettings weights={value.ragas_query_distribution} disabled={disabled} language={language} onChange={next => change('ragas_query_distribution', next)} />
        <div className="field"><span className="label-with-tip"><label htmlFor={personaId}>{t('模拟提问者（可选）')}</label><InfoTip label={t('模拟提问者说明')} text={t('模拟谁来提问，影响问题的关注点和表达方式。留空时根据资料自动生成。每行填写“名称 | 角色描述”，可填写多个角色；与问题类型占比分开设置。')} /></span><textarea id={personaId} aria-label={t('模拟提问者（可选）')} placeholder={t('例如：普通客户 | 不熟悉产品术语，关注使用方法、费用和退款条件')} disabled={disabled} value={personaDraft} onChange={e=>updatePersonas(e.target.value)} />
          <div className="dataset-style-examples" role="group" aria-label={t('模拟提问者示例')}><span>{t('点击示例填入')}</span>{[
            ['普通客户','普通客户 | 不熟悉产品术语，关注使用方法、费用和退款条件'],
            ['客服专员','客服专员 | 关注业务规则、例外情况和处理流程'],
            ['技术人员','技术人员 | 熟悉技术术语，关注接口限制、故障原因和排查步骤'],
          ].map(([label,text])=><button type="button" key={label} disabled={disabled} aria-pressed={personaDraft===t(text)} onClick={()=>updatePersonas(t(text))}>{t(label)}</button>)}<button type="button" disabled={disabled} aria-pressed={!personaDraft.trim()} onClick={()=>updatePersonas('')}>{t('留空自动生成')}</button></div>
        </div>
      </>}
      {deepEval && !reviewOutside && reviewField}
      <div className="dataset-option-grid">{([
        ['quality_threshold', '质量阈值', 0, 1, 0.05, '范围 0–1，默认 0.5。越高越严格，低于阈值的问题会尝试改写；可能增加耗时和费用。达到评审上限后仍可能保留低分项。'],
        ['max_quality_retries', '质量评审次数上限', 1, 5, 1, '范围 1–5，默认 3。限制低分问题的质量评审尝试次数；调高可增加改写机会，也会增加模型调用。'],
        ['num_evolutions', '演化轮数', 0, 3, 1, '范围 0–3，默认 1。对每个初始问题做几轮改写，让问题更具体或更多样；0 表示不演化。轮数越多，调用越多。'],
        ['context_size', '每组上下文片段数', 1, 5, 1, '范围 1–5，默认 2。每批选取多少个资料片段供模型出题。增加可覆盖更多信息，也会增加输入长度；仅文档和 Dify 生成生效。'],
      ] as const).filter(([key]) => deepEval).map(([key, label, min, max, step, help]) => <GenerationSlider key={key} label={t(label)} helpId={`generation-${key}-help`} help={key === 'quality_threshold' ? [t(help), t('DeepEval 达到评审次数上限后仍可能保留低分项；质量分与待复核标记保存在 additional_metadata。')].join(' ') : t(help)} min={min} max={max} step={step} disabled={disabled} value={value[key]} language={language} onChange={next => change(key, next)} />)}</div>
      {deepEval && <EvolutionSettings weights={value.evolutions} rounds={value.num_evolutions} scratch={scratch} disabled={disabled} language={language} onChange={next => change('evolutions', next)} />}
    </div></details>
    {!scratch && <div className="dataset-answer-settings" role="group" aria-label={t('参考答案设置')}>
      <div className="label-with-tip"><label className="dataset-check"><input type="checkbox" checked={value.include_expected_output} disabled={disabled} onChange={event => change('include_expected_output', event.target.checked)} />{t('生成参考答案')}</label><InfoTip label={t('查看说明')} text={t('需要参考答案的指标才建议开启。开启后依据参考上下文生成答案，会增加输出和费用；检索排序指标通常不需要。没有参考上下文的种子仅生成问题。')} /></div>
      {value.include_expected_output && <GenerationStyleField field="expected_output_format" value={value.expected_output_format} onChange={next => change('expected_output_format', next)} language={language} disabled={disabled} />}
    </div>}
  </>;
}

function ItemEditor({ item, dataset, language, onClose, onSaved }: { item: DatasetItem | null; dataset: Dataset; language: Language; onClose: () => void; onSaved: () => void }) {
  const t = (value: string) => tr(language, value);
  const { id: _id, dataset_id: _datasetId, created_at: _created, updated_at: _updated, status: _status, origin: _origin, generation_trace_id: _trace, generation_record_id: _record, ...golden } = item || {};
  const [content, setContent] = useState(JSON.stringify(item ? golden : { input: '', expected_output: null, context: [], additional_metadata: {} }, null, 2));
  const [status, setStatus] = useState(item?.status || 'active');
  const [question, setQuestion] = useState(item?.input || '');
  const [jsonMode, setJsonMode] = useState(false);
  const [answer, setAnswer] = useState(item?.expected_output || '');
  const [context, setContext] = useState((item?.context || []).join('\n\n'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const readOnly = dataset.status === 'generating';
  return <WorkspaceDialog title={t(item ? '查看或编辑数据项' : '添加数据项')} language={language} busy={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); if (readOnly) return; setError('');
    let value;
    try {
      const record = jsonMode ? JSON.parse(content) : { ...(item ? JSON.parse(content) : {}), input: question, expected_output: answer || null, context: context.split(/\n\s*\n/).map(value => value.trim()).filter(Boolean) };
      if (!record || Array.isArray(record) || typeof record.input !== 'string' || !record.input.trim()) throw new Error();
      value = { ...record, status };
    } catch { setError(t('请填写有效的 Golden JSON，input 必须为非空字符串。')); return; }
    setBusy(true);
    try { await datasetApi.saveItem(dataset.id, item?.id, value); onSaved(); }
    catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  }}><div className="workspace-dialog-body dataset-item-body">{item && <label className="dataset-check"><input type="checkbox" checked={jsonMode} disabled={busy || readOnly} onChange={event => {if(event.target.checked){setContent(JSON.stringify({...JSON.parse(content),input:question,expected_output:answer || null,context:context.split(/\n\s*\n/).map(value => value.trim()).filter(Boolean)},null,2));setJsonMode(true);}else{try{const record=JSON.parse(content);if(typeof record.input !== 'string' || (record.context && (!Array.isArray(record.context) || record.context.some((value:unknown)=>typeof value !== 'string'))))throw new Error();setQuestion(record.input);setAnswer(record.expected_output || '');setContext((record.context || []).join('\n\n'));setJsonMode(false);setError('');}catch{setError(t('请填写有效的 Golden JSON，input 必须为非空字符串。'));}}}} />{t('高级 JSON 编辑')}</label>}{jsonMode ? <><p className="workspace-muted">{t('使用 DeepEval Golden JSON 编辑全部字段。context 是参考上下文，retrieval_context 是实际检索结果。')}</p>
    <label className="field"><span>Golden JSON</span><textarea aria-label="Golden JSON" className="dataset-json" rows={20} required value={content} disabled={busy || readOnly} onChange={event => setContent(event.target.value)} /></label>
    </> : <>
      <label className="field"><span>{t('输入 / 问题')}</span><textarea aria-label={t('输入 / 问题')} required rows={3} maxLength={20000} disabled={busy || readOnly} value={question} onChange={event => setQuestion(event.target.value)} /></label>
      <label className="field"><span>{t('参考答案')}</span><textarea aria-label={t('参考答案')} rows={3} maxLength={50000} disabled={busy || readOnly} value={answer} onChange={event => setAnswer(event.target.value)} /></label>
      <label className="field"><span className="label-with-tip">{t('参考上下文')}<InfoTip label={t('查看说明')} text={t('可选。不同资料片段之间留一个空行；手工添加不会调用模型。')} /></span><textarea aria-label={t('参考上下文')} rows={4} maxLength={100000} disabled={busy || readOnly} value={context} onChange={event => setContext(event.target.value)} /></label>
    </>}
    <label className="field"><span>{t('状态')}</span><select disabled={busy || readOnly} value={status} onChange={event => setStatus(event.target.value as 'active' | 'archived')}><option value="active">{t('启用')}</option><option value="archived">{t('归档')}</option></select></label>
    {error && <p className="error-text" role="alert">{error}</p>}
  </div><div className="workspace-dialog-footer"><button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('关闭')}</button>{!readOnly && <button className="primary compact" disabled={busy || readOnly}>{t(busy ? '保存中…' : '保存')}</button>}</div></form></WorkspaceDialog>;
}

function DatasetMetadata({ dataset, language, onClose, onSaved, embedded = false }: { embedded?: boolean; dataset: Dataset; language: Language; onClose: () => void; onSaved: (value: Dataset) => void }) {
  const t = (value: string) => tr(language, value);
  const [name, setName] = useState(dataset.name);
  const [purpose, setPurpose] = useState<DatasetPurpose>(dataset.evaluation_purpose || 'both');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <SettingsContainer embedded={embedded} title={t('编辑评测数据集')} language={language} busy={busy} onClose={onClose}><form onSubmit={async event => { event.preventDefault(); if (dataset.status === 'generating') return; setBusy(true); setError(''); try { onSaved(await datasetApi.update(dataset.id, { name, description: dataset.description, evaluation_purpose:purpose })); } catch (reason) { setError(message(reason)); } finally { setBusy(false); } }}><div className="workspace-dialog-body"><DatasetPurposeField value={purpose} onChange={setPurpose} language={language} disabled={busy} /><label className="field"><span>{t('名称')}</span><input required maxLength={120} disabled={busy} value={name} onChange={event => setName(event.target.value)} /></label>{error && <p role="alert" className="error-text">{error}</p>}</div><div className="workspace-dialog-footer">{!embedded && <button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('取消')}</button>}<button className="primary compact" disabled={dataset.status === 'generating' || busy || !name.trim()}>{t(busy ? '保存中…' : '保存')}</button></div></form></SettingsContainer>;
}

function ImportItems({ dataset, language, onClose, onSaved }: { dataset: Dataset; language: Language; onClose: () => void; onSaved: () => void }) {
  const t = (value: string) => tr(language, value);
  const [file, setFile] = useState<File | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  return <WorkspaceDialog title={t('追加导入')} language={language} busy={busy} onClose={onClose}><form onSubmit={async event => { event.preventDefault(); if (!file) return; setBusy(true); setError(''); try { const form = new FormData(); form.set('file', file); await datasetApi.append(dataset.id, form); onSaved(); } catch (reason) { setError(message(reason)); } finally { setBusy(false); } }}><div className="workspace-dialog-body"><ImportHelp language={language} /><label className="field"><span>{t('数据集文件')}</span><input required type="file" accept=".csv,.json,.jsonl" disabled={busy} onChange={event => setFile(event.target.files?.[0] || null)} /></label>{file && file.size > 10 * 1024 * 1024 && <p className="error-text">{t('文件总大小不能超过 10 MB')}</p>}{error && <p className="error-text" role="alert">{error}</p>}</div><div className="workspace-dialog-footer"><button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('取消')}</button><button className="primary compact" disabled={busy || !file || file.size > 10 * 1024 * 1024}>{t(busy ? '提交中…' : '追加导入')}</button></div></form></WorkspaceDialog>;
}

function DatasetMaintenance({ mode, dataset, sources, catalog, language, onClose, onSaved, embedded = false }: {
  embedded?: boolean;
  mode: 'config' | 'documents' | 'dify' | 'build'; dataset: Dataset; sources: DatasetSources | null;
  catalog: Catalog | null; language: Language; onClose: () => void; onSaved: (value: Dataset) => void;
}) {
  const t = (value: string) => tr(language, value);
  const { max_concurrent: _legacyConcurrency, ...config } = dataset.generation_config || dataset.generation_options || {};
  const [advanced, setAdvanced] = useState({ ...defaultGenerationOptions, ...config, generation_backend: config?.generation_backend === 'ragas' ? 'deepeval' : (config?.generation_backend || 'deepeval'),
    include_expected_output: config?.include_expected_output ?? dataset.evaluation_purpose !== 'retrieval',
    critic_llm: selectionValue(config?.critic_llm || null), evolutions: config?.evolutions || defaultGenerationOptions.evolutions });
  const [llm, setLlm] = useState(selectionValue(config?.llm || null));
  const [size, setSize] = useState(config?.size || 50);
  const [method, setMethod] = useState(config?.method || 'contexts');
  const [prompt, setPrompt] = useState(config?.prompt || '');
  const [files, setFiles] = useState<File[]>([]);
  const [dify, setDify] = useState<string[]>([]);
  const [buildMode, setBuildMode] = useState<'append' | 'replace_generated'>('append');
  const [settings, setSettings] = useState<DatasetSettings | null>(null);
  const [loading, setLoading] = useState(mode === 'config');
  const [purpose, setPurpose] = useState<DatasetPurpose>(dataset.evaluation_purpose || 'both');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const retrievalOnly = dataset.evaluation_purpose === 'retrieval';
  const choices = llmChoices(catalog, language);
  const difyChoices = catalog?.benchmarks.find(value => value.id === 'dify-retrieve')?.providers || [];
  const staleLlm = !!llm && !choices.some(choice => choice.value === llm);
  const staleCritic = advanced.generation_backend === 'deepeval' && !!advanced.critic_llm && advanced.critic_llm !== 'generation' && !choices.some(choice => choice.value === advanced.critic_llm);
  const title = { config: '生成配置', documents: '导入文档', dify: '导入知识库来源', build: '重新构建' }[mode];
  const hasContexts = (sources?.items.length || 0) > 0;
  const missingSource = method === 'contexts' ? !hasContexts : method === 'goldens' ? !dataset.item_count : !advanced.task.trim();
  useEffect(() => { if (retrievalOnly && method !== 'contexts') setMethod('contexts'); }, [retrievalOnly, method]);
  useEffect(() => {
    if (mode !== 'config') return;
    let active = true;
    datasetApi.settings().then(value => { if (active) setSettings(value); })
      .catch(reason => { if (active) setError(message(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [mode]);
  return <SettingsContainer embedded={embedded} title={t(title)} language={language} busy={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); if (dataset.status === 'generating') return; setBusy(true); setError('');
    try {
      let value: Dataset;
      if (mode === 'config') {
        const generationModel = llm ? JSON.parse(llm) : null;
        if (advanced.generation_backend === 'deepeval' && advanced.critic_llm === 'generation' && !generationModel && !settings?.default_llm) throw new Error(t('请先选择生成模型，或在空间设置中配置默认模型。'));
        const criticModel = advanced.generation_backend !== 'deepeval' ? null : advanced.critic_llm === 'generation' ? generationModel || settings?.default_llm : advanced.critic_llm ? JSON.parse(advanced.critic_llm) : null;
        value = await datasetApi.configure(dataset.id, { ...advanced, llm: generationModel, critic_llm: criticModel,
          size, method, seed_dataset_id: method === 'goldens' ? dataset.id : null, dify_config_id: null, dify_config_ids: [], prompt: prompt.trim() ? prompt : null, language });
      } else if (mode === 'documents') {
        const form = new FormData(); files.forEach(file => form.append('files', file));
        value = await datasetApi.documents(dataset.id, form);
      } else if (mode === 'dify') {
        const form = new FormData(); dify.forEach(id => form.append('config_ids', id));
        value = await datasetApi.syncDify(dataset.id, form);
      } else value = await datasetApi.build(dataset.id, buildMode);
      onSaved(value);
    } catch (reason) { setError(message(reason)); } finally { setBusy(false); }
  }}><div className="workspace-dialog-body dataset-create-body">
    {mode === 'config' && <>
      <div className="field"><span className="label-with-tip">{t('数据集生成模型')}<InfoTip label={t('查看说明')} text={[t('配置仅对当前数据集生效，保存不会调用模型。下一次追加生成或重新构建使用新配置；继续失败任务仍沿用原任务配置。'), t('会调用所选 LLM 并产生相应费用。')].join(' ')} /></span><SearchableSelect label={t('数据集生成模型')} language={language} disabled={dataset.status === 'generating' || busy || loading} value={llm} onChange={setLlm} options={[{ value: '', label: t('使用空间默认模型') + ` · ${settings?.default_llm?.model || t('未配置')}` }, ...choices]} />{staleLlm && <small className="error-text">{t('原默认模型已失效，请重新选择')}</small>}</div>
      {retrievalOnly ? <p className="field-help">{t('检索评测数据集固定使用已同步的 Dify 知识库片段，不支持手动、导入文档或按任务生成。')}</p> : <label className="field"><span>{t('生成方式')}</span><select aria-label={t('生成方式')} disabled={busy} value={method} onChange={event => setMethod(event.target.value as typeof method)}><option value="contexts">{t('从已导入的资料生成')}</option><option value="goldens">{t('扩写当前数据集')}</option><option value="scratch">{t('按任务生成')}</option></select></label>}
      <label className="field"><span className="label-with-tip">{t('每次生成数量')}<InfoTip label={t('查看说明')} text={t('每次生成 1–500 条新数据，追加时不包含已有条数。')} /></span><input aria-label={t('每次生成数量')} required type="number" min={1} max={500} step={1} disabled={busy} value={size} onChange={event => setSize(Number(event.target.value))} /></label>
      <GenerationControls value={advanced} onChange={setAdvanced} catalog={catalog} language={language} disabled={dataset.status === 'generating' || busy || loading} scratch={method === 'scratch'} contexts={method === 'contexts'} settings={settings} staleCritic={staleCritic} purpose={dataset.evaluation_purpose || 'both'} reviewOutside />
      <label className="field"><span className="label-with-tip">{t('数据集专用提示词')}<InfoTip label={t('查看说明')} text={t('留空时使用空间构建提示词；填写后仅影响当前数据集的后续生成。')} /></span><textarea aria-label={t('数据集专用提示词')} rows={5} maxLength={20000} disabled={busy} value={prompt} onChange={event => setPrompt(event.target.value)} /></label>
    </>}
    {mode === 'documents' && <>
      <p className="field-help">{t('文档将作为生成问答的来源资料保存，不会立即调用模型，也不会修改已有问答。重复导入同名同内容文档会自动去重。')}</p>
      <label className="field"><span className="label-with-tip">{t('源文档')}<InfoTip label={t('查看说明')} text={t('支持 TXT、Markdown、PDF、DOCX、CSV，最多 20 个文件 / 10 MB；扫描 PDF 需先 OCR。')} /></span><input aria-label={t('源文档')} required type="file" multiple accept=".txt,.md,.markdown,.pdf,.docx,.csv" disabled={busy} onChange={event => setFiles(Array.from(event.target.files || []))} /></label>
      {!!files.length && <p className="field-help">{files.map(file => file.name).join('、')}</p>}
    </>}
    {mode === 'dify' && <>
      {!!sources?.items.filter(source => source.kind === 'dify').length && <p className="field-help">{t('已有知识库')}：{sources.items.filter(source => source.kind === 'dify').map(source => source.name).join('、')}</p>}
      <p className="field-help">{t('可选择多个知识库。导入会保留其他来源和已有样本；已导入的所选知识库会更新为最新片段。导入后可点击“重新构建”追加生成样本。')}</p>
      <div className="field"><span className="label-with-tip">{t('Dify 知识库')}<InfoTip label={t('查看说明')} text={t('使用 Dify 配置中的知识库 ID 和 Dataset API Key，自动分页拉取已启用的文档分段。')} /></span><KnowledgeSelector choices={difyChoices} value={dify} onChange={setDify} language={language} disabled={busy} /></div>
      {!difyChoices.length && <p className="field-help">{t('暂无可用的 Dify 知识库，请先添加 Dify 配置。')}</p>}
    </>}
    {mode === 'build' && <>
      <p className="field-help">{t('使用已保存的模型、生成参数和来源资料。开始后会调用模型并产生费用。')}</p>
      <dl className="dataset-build-summary"><div><dt>{t('生成后端')}</dt><dd>{config?.generation_backend==='ragas_native' ? 'RAGAS' : 'DeepEval'}</dd></div><div><dt>{t('生成方式')}</dt><dd>{t({ contexts: '从已导入的资料生成', goldens: '扩写当前数据集', scratch: '按任务生成' }[method])}</dd></div><div><dt>{t('数据集生成模型')}</dt><dd>{config?.llm?.model || t('使用空间默认模型')}</dd></div>{config?.generation_backend === 'deepeval' && <div><dt>{t('质量评审模型')}</dt><dd>{config?.critic_llm?.model || t('使用空间质量评审模型')}</dd></div>}<div><dt>{t('每次生成数量')}</dt><dd>{size}</dd></div><div><dt>{t('已有数据')}</dt><dd>{dataset.item_count}</dd></div></dl>
      <label className="field"><span>{t('构建方式')}</span><select aria-label={t('构建方式')} disabled={busy} value={buildMode} onChange={event => setBuildMode(event.target.value as typeof buildMode)}><option value="append">{t('追加生成（保留全部已有数据）')}</option><option value="replace_generated">{t('重新构建（替换 AI 生成数据）')}</option></select></label>
      <p className={buildMode === 'replace_generated' ? 'dataset-feedback warning' : 'field-help'}>{t(buildMode === 'replace_generated' ? '新结果全部生成成功后，才替换原有 AI 生成数据。手工添加、文件导入、人工编辑过及无法确认来源的旧数据会保留；生成失败时原数据不变，重试会重新生成整批数据。' : '生成指定数量的新问答并追加到当前数据集。失败时保留已追加的数据，可继续构建。')}</p>
      {missingSource && <p className="error-text" role="alert">{t('请先在生成配置中选择生成方式并填写任务，或导入文档、同步知识库、添加种子数据后再构建。')}</p>}
    </>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </div><div className="workspace-dialog-footer">{!embedded && <button type="button" className="ghost" disabled={busy} onClick={onClose}>{t('取消')}</button>}<button className="primary compact" disabled={dataset.status === 'generating' || busy || loading || (mode === 'config' && (staleLlm || staleCritic || !validEvolutionSettings(advanced, method === 'scratch') || !validRagasDistribution(advanced) || !settings || (method === 'scratch' && !advanced.task.trim()))) || (mode === 'documents' && (!files.length || files.length > 20 || files.reduce((sum, file) => sum + file.size, 0) > 10 * 1024 * 1024)) || (mode === 'dify' && !dify.length) || (mode === 'build' && missingSource)}>{t(busy ? '提交中…' : mode === 'config' ? '保存配置' : mode === 'documents' ? '导入文档' : mode === 'dify' ? '导入来源' : buildMode === 'replace_generated' ? '开始重新构建' : '开始追加生成')}</button></div></form></SettingsContainer>;
}

function DatasetPurposeField({value,onChange,language,disabled}:{value:DatasetPurpose;onChange:(value:DatasetPurpose)=>void;language:Language;disabled:boolean}) {
  const t=(text:string)=>tr(language,text);
  return <label className="field"><span className="label-with-tip">{t('数据集用途')}<InfoTip label={t('数据集用途说明')} text={t('用途用于筛选适用的评测，不会自动补齐答案或检索标注。两者通用可用于两种评测；具体数据要求由所选指标决定。')} /></span><select aria-label={t('数据集用途')} value={value} disabled={disabled} onChange={e=>onChange(e.target.value as DatasetPurpose)}>{Object.entries(datasetPurposeLabels).map(([value,label])=><option key={value} value={value}>{t(label)}</option>)}</select></label>;
}

function SettingsContainer({embedded,children,...props}: {embedded: boolean; children: ReactNode; title: string; language: Language; busy: boolean; onClose: () => void}) {
  return embedded ? <div className="dataset-inline-form"><fieldset disabled={props.busy}>{children}</fieldset></div> : <WorkspaceDialog {...props}>{children}</WorkspaceDialog>;
}

function KnowledgeSelector({choices,value,onChange,language,disabled}: {choices: Array<{id:string;label:string;dataset_id?:string|null}>;value:string[];onChange:(value:string[])=>void;language:Language;disabled:boolean}) {
  const t = (text:string) => tr(language,text);
  return <><SearchableMultiSelect label={t('Dify 知识库')} placeholder={t('选择知识库（可多选）')} searchPlaceholder={t('搜索知识库')} language={language} disabled={disabled} value={value} onChange={onChange} options={choices.map(choice=>({value:choice.id,label:choice.label}))} />
    {!!value.length && <ul className="dataset-selected-knowledge">{value.map(id => {const choice=choices.find(item=>item.id===id);return <li key={id}><span>{choice?.label || t('知识库配置不可用')}</span><button type="button" className="icon-button" disabled={disabled} aria-label={`${t('移除来源')} ${choice?.label || t('知识库配置不可用')}`} onClick={()=>onChange(value.filter(item=>item!==id))}><Trash2 aria-hidden="true" /></button></li>;})}</ul>}
  </>;
}
