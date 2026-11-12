import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Plus, RefreshCw, X } from 'lucide-react';
import { api } from '../api';
import { tr, type Language } from '../i18n';
import type { ServiceConfig } from '../types';
import { InfoTip } from './InfoTip';
import { SelectionCheckbox } from './BulkSelection';

type DatasetChoice = { id: string; name: string };
// Each save also performs a server-side Dify connectivity check; bound concurrency so bulk saves stay fast without flooding either service.
const SAVE_CONCURRENCY = 6;

function apiScope(value: string) {
  try {
    const url = new URL(value.trim());
    url.pathname = url.pathname.replace(/\/+$/, '').replace(/\/v1$/, '') + '/v1';
    return url.toString();
  } catch { return value.trim(); }
}

export function DifyConfigDialog({ config, configs, language, onSaved, onClose }: {
  config: ServiceConfig | null; configs: ServiceConfig[]; language: Language;
  onSaved: (saved: ServiceConfig[]) => void; onClose: () => void;
}) {
  const t = (value: string) => tr(language, value);
  const [benchmark, setBenchmark] = useState(config?.models[0]?.benchmark || config?.benchmark || 'dify-retrieve');
  const [mode, setMode] = useState<'list' | 'manual'>(config ? 'manual' : 'list');
  const [url, setUrl] = useState(config?.models[0]?.base_url || config?.base_url || 'https://api.dify.ai');
  const [key, setKey] = useState('');
  const [name, setName] = useState(config?.name || '');
  const [datasetId, setDatasetId] = useState(config?.dataset_id || '');
  const [datasets, setDatasets] = useState<DatasetChoice[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState<'fetch' | 'save' | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [progress, setProgress] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const urlInput = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const chat = benchmark === 'dify-chat';
  const listMode = !chat && mode === 'list' && !config;
  const existingIds = new Set(configs.filter(item => item.id !== config?.id && item.provider === 'dify'
    && (item.models.some(model => model.benchmark === benchmark) || item.benchmark === benchmark)
    && apiScope(item.models[0]?.base_url || item.base_url || '') === apiScope(url)).map(item => item.dataset_id));
  const isAdded = (id: string) => existingIds.has(id) || added.has(id);
  const canChoose = (item: DatasetChoice) => !isAdded(item.id);
  const visible = datasets.filter(item => `${item.name} ${item.id}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const pending = datasets.filter(item => selected.has(item.id) && canChoose(item));
  const duplicate = !listMode && benchmark === 'dify-retrieve' && existingIds.has(datasetId.trim());

  useEffect(() => {
    alive.current = true;
    const element = dialog.current;
    element?.showModal();
    urlInput.current?.focus();
    return () => { alive.current = false; if (element?.open) element.close(); };
  }, []);

  function resetResults() {
    setDatasets([]); setSelected(new Set()); setAdded(new Set()); setPage(0); setHasMore(false);
    setSearch(''); setError(''); setNotice('');
  }
  function toggleVisible(checked: boolean) {
    setSelected(current => {
      const next = new Set(current);
      for (const item of visible) if (canChoose(item)) { if (checked) next.add(item.id); else next.delete(item.id); }
      return next;
    });
  }
  async function fetchDatasets(nextPage = 1) {
    if (busy || !urlInput.current?.reportValidity()) return;
    setBusy('fetch'); setError(''); setNotice('');
    try {
      const result = await api.difyNames({ provider: 'dify', benchmark, server_url: url.trim(), api_key: key || undefined, config_id: config?.id, page: nextPage });
      if (!alive.current) return;
      setDatasets(current => [...new Map([...(nextPage === 1 ? [] : current), ...result.names].map(item => [item.id, item])).values()]);
      if (nextPage === 1) setSelected(new Set());
      setPage(nextPage); setHasMore(result.has_more && nextPage < 10000);
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? t(reason.message) : t('获取知识库列表失败')); }
    finally { if (alive.current) setBusy(null); }
  }
  async function fetchName() {
    if (busy || !urlInput.current?.reportValidity()) return;
    setBusy('fetch'); setError(''); setNotice('');
    try {
      const result = await api.difyNames({ provider: 'dify', benchmark, server_url: url.trim(), api_key: key || undefined,
        config_id: config?.id, dataset_id: benchmark === 'dify-retrieve' ? datasetId.trim() : undefined });
      if (!alive.current) return;
      if (result.names.length !== 1) throw new Error(t('Dify 未返回知识库名称，请检查知识库 ID 和权限。'));
      setName(result.names[0].name.slice(0, 120)); setNotice(t('名称已填充，保存后才会更新配置。'));
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? t(reason.message) : t('获取名称失败')); }
    finally { if (alive.current) setBusy(null); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || duplicate || (listMode && !pending.length)) return;
    setBusy('save'); setError(''); setNotice('');
    const choices = listMode ? pending : [{ id: datasetId.trim(), name: name.trim() }];
    const saved: Array<ServiceConfig | undefined> = new Array(choices.length);
    const failed: Array<string | undefined> = new Array(choices.length);
    const savedIds: string[] = [];
    let nextIndex = 0;
    let completed = 0;
    setProgress(`${t('保存中…')} 0 / ${choices.length}`);

    async function saveOne(index: number) {
      const item = choices[index];
      const payload = { provider: 'dify',
        app_id: chat && config && !key && apiScope(config.models[0]?.base_url || config.base_url || '') === apiScope(url) ? config.app_id : null, name: item.name.slice(0, 120), icon: config?.icon || 'cube',
        models: [{ name: null, benchmark, base_url: url.trim() }],
        dataset_id: benchmark === 'dify-retrieve' ? item.id : null, api_key: key || null };
      try {
        saved[index] = config ? await api.updateServiceConfig(config.id, payload) : await api.createServiceConfig(payload);
        savedIds.push(item.id);
      } catch (reason) {
        failed[index] = `${item.name}: ${reason instanceof Error ? t(reason.message) : t('保存服务配置失败')}`;
      } finally {
        completed += 1;
        if (alive.current) setProgress(`${t('保存中…')} ${completed} / ${choices.length}`);
      }
    }

    async function worker() {
      while (alive.current) {
        const index = nextIndex++;
        if (index >= choices.length) return;
        await saveOne(index);
      }
    }

    await Promise.all(Array.from({ length: Math.min(SAVE_CONCURRENCY, choices.length) }, () => worker()));
    if (!alive.current) return;
    const savedConfigs = saved.filter((item): item is ServiceConfig => Boolean(item));
    const failedMessages = failed.filter((item): item is string => Boolean(item));
    if (savedConfigs.length) {
      onSaved(savedConfigs);
      setAdded(current => new Set([...current, ...savedIds]));
      setSelected(current => { const next = new Set(current); savedIds.forEach(id => next.delete(id)); return next; });
    }
    setBusy(null); setProgress('');
    if (failedMessages.length) {
      setError(failedMessages.join('\n'));
      if (savedConfigs.length) setNotice(`${t('已添加')} ${savedConfigs.length} · ${t('失败')} ${failedMessages.length}`);
    } else onClose();
  }

  return <dialog ref={dialog} className="dify-modal dify-native-dialog dify-dataset-dialog" aria-labelledby="dify-editor-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dify-modal-head"><div><h2 id="dify-editor-title">{t(config ? '编辑 Dify 配置' : '新增 Dify 配置')}</h2><p>{t(chat ? '填写 Base URL、Chat API Key 和配置名称；可获取该密钥对应的应用名称。' : '获取列表不会保存配置；选择知识库后添加，也可手动填写。')}</p></div><button type="button" className="dify-close" aria-label={t('关闭')} disabled={!!busy} onClick={onClose}><X aria-hidden="true" /></button></div>
    <form autoComplete="off" onSubmit={save}>
      <div className="dify-modal-body">
        <label className="field"><span className="label-with-tip">{t('Dify 类型')}<InfoTip label={t('查看说明')} text={t('知识库检索直接调用 Dataset API；聊天应用调用已发布应用的 Chat API。')} /></span><select aria-label={t('Dify 类型')} value={benchmark} disabled={!!busy} onChange={event => { setBenchmark(event.target.value); setKey(''); resetResults(); }}><option value="dify-retrieve">{t('知识库检索')}</option><option value="dify-chat">{t('聊天应用')}</option></select></label>
        <label className="field"><span className="label-with-tip">Dify Base URL<InfoTip label={t('查看说明')} text={t(chat ? '填写用于调用应用的 Service API Base URL，可包含 /v1。' : '填写 Dify 实例的 Base URL，可包含 /v1；通过 Dataset API 获取当前密钥有权限访问的知识库。')} /></span><input ref={urlInput} aria-label="Dify Base URL" required type="url" maxLength={2048} disabled={!!busy} value={url} onChange={event => { setUrl(event.target.value); resetResults(); }} placeholder="https://api.dify.ai/v1" /></label>
        <label className="field"><span className="label-with-tip">{benchmark === 'dify-chat' ? 'Chat API Key' : 'Dataset API Key'}<InfoTip label={t('查看说明')} text={t(config ? '留空表示使用已保存密钥；更换服务地址时需填写新密钥。' : benchmark === 'dify-chat' ? '在 Dify 应用的 API 访问页面创建，用于调用已发布应用。' : '在 Dify 知识库的 API 访问页面创建；获取列表仅展示该密钥有权限访问的知识库。')} /></span><input aria-label={benchmark === 'dify-chat' ? 'Chat API Key' : 'Dataset API Key'} type="password" autoComplete="new-password" required={listMode} disabled={!!busy} value={key} onChange={event => { setKey(event.target.value); resetResults(); }} placeholder={config?.has_api_key ? config.api_key_masked || '••••••••' : config ? t('留空表示保留现有密钥') : benchmark === 'dify-chat' ? t('填写 Chat API Key') : t('填写 Dataset API Key')} /></label>
        {!chat && !config && <div className="dify-discovery-actions" aria-label={t('添加方式')}><button type="button" className="dify-secondary-button" aria-pressed={mode === 'list'} disabled={!!busy} onClick={() => { setMode('list'); setError(''); }}>{t('从列表添加')}</button><button type="button" className="dify-secondary-button" aria-pressed={mode === 'manual'} disabled={!!busy} onClick={() => { setMode('manual'); setError(''); }}>{t('手动添加')}</button></div>}
        {listMode ? <>
          <div className="dify-discovery-actions"><button type="button" className="dify-secondary-button" disabled={!!busy || !url.trim() || !key.trim()} onClick={() => void fetchDatasets()}><RefreshCw aria-hidden="true" />{t(busy === 'fetch' ? '获取中…' : '获取知识库列表')}</button><span className="dify-discovery-hint">{t('已选择')} {pending.length}</span></div>
          {page > 0 && <div className="dify-discovery-list">
            <div className="dify-discovery-list-head"><strong>{t('知识库')} · {datasets.length}</strong><input aria-label={t('搜索知识库名称或 ID')} placeholder={t('搜索知识库名称或 ID')} value={search} disabled={busy === 'save'} onChange={event => setSearch(event.target.value)} /></div>
            {datasets.length > 0 && <div className="dify-dataset-selection"><SelectionCheckbox label={t('选择当前搜索结果')} checked={visible.some(canChoose) && visible.filter(canChoose).every(item => selected.has(item.id))} mixed={visible.some(item => canChoose(item) && selected.has(item.id)) && visible.some(item => canChoose(item) && !selected.has(item.id))} disabled={!!busy || !visible.some(canChoose)} onChange={toggleVisible} /><span>{t('选择当前搜索结果')}</span></div>}
            <div className="dify-discovery-scroll">{visible.map(item => <div className="dify-discovery-row" key={item.id}><label><input type="checkbox" aria-label={`${t('选择知识库')} ${item.name} ${item.id}`} checked={selected.has(item.id) && canChoose(item)} disabled={!!busy || !canChoose(item)} onChange={event => { const checked = event.target.checked; setSelected(current => { const next = new Set(current); if (checked) next.add(item.id); else next.delete(item.id); return next; }); }} /><span className="dify-dataset-choice"><strong>{item.name}</strong><small>{item.id}</small></span></label>{isAdded(item.id) && <small>{t('已添加')}</small>}</div>)}{!visible.length && <p className="dify-empty compact" role="status">{t(datasets.length ? '没有匹配的知识库' : '未找到知识库，请检查密钥权限，或手动添加。')}</p>}</div>
            {hasMore && <button type="button" className="ghost" disabled={!!busy} onClick={() => void fetchDatasets(page + 1)}>{t('加载更多知识库')}</button>}
          </div>}
        </> : <>
          {benchmark === 'dify-retrieve' && <label className="field"><span className="label-with-tip">{t('知识库 ID')}<InfoTip label={t('查看说明')} text={t('知识库的唯一标识，可从 Dify 知识库页面 URL 复制；测试计划与 Playground 自动使用此 ID。')} /></span><input aria-label={t('知识库 ID')} required maxLength={256} disabled={!!busy} value={datasetId} onChange={event => { setDatasetId(event.target.value); setError(''); setNotice(''); }} placeholder={t('填写 Dify 知识库 ID')} /></label>}
          <div className="field"><label htmlFor="dify-config-name">{t('配置名称')}</label><div className="dify-name-input"><input id="dify-config-name" required maxLength={120} disabled={!!busy} value={name} onChange={event => setName(event.target.value)} placeholder={t('例如 Dify 生产环境')} /><button type="button" className="icon-button" aria-label={t(benchmark === 'dify-chat' ? '获取应用名称' : '获取知识库名称')} disabled={!!busy || !url.trim() || (benchmark === 'dify-retrieve' && !datasetId.trim())} onClick={() => void fetchName()}><RefreshCw aria-hidden="true" /></button></div></div>
          {duplicate && <p className="error-text" role="alert">{t('该知识库已添加，请编辑已有配置。')}</p>}
        </>}
      </div>
      {notice && <p className="dify-dialog-success" role="status">{notice}</p>}
      {error && <p className="dify-dialog-error" role="alert">{error}</p>}
      {busy && <p className="dify-dataset-progress" role="status">{busy === 'save' ? progress : t('获取中…')}</p>}
      <div className="dify-modal-footer"><span className="dify-footer-spacer" /><button type="button" className="dify-secondary-button" disabled={!!busy} onClick={onClose}>{t('取消')}</button><button className="dify-blue-button" disabled={!!busy || !url.trim() || (listMode ? !pending.length : !name.trim() || duplicate || (benchmark === 'dify-retrieve' && !datasetId.trim()))}>{listMode && <Plus aria-hidden="true" />}{busy === 'save' ? t('保存中…') : listMode ? `${t('添加所选知识库')} (${pending.length})` : t('保存')}</button></div>
    </form>
  </dialog>;
}
