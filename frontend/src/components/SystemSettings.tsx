import { useEffect, useRef, useState } from 'react';
import { Settings2, Bot, Database, FlaskConical, Save } from 'lucide-react';
import { api } from '../api';
import { tr, type Language } from '../i18n';
import type { Catalog } from '../types';
import { llmChoices, selectionValue } from '../datasetApi';
import { PageHeader } from './PageHeader';
import { InfoTip } from './InfoTip';
import { SearchableSelect } from './SearchableSelect';
import { DatasetSettings } from './DatasetSettings';
import '../space-settings.css';

const sections = [
  { id: 'generation', label: '数据集生成', icon: Database },
  { id: 'evaluation', label: '质量评测', icon: FlaskConical },
  { id: 'report', label: 'AI 测试报告', icon: Bot },
] as const;
type Section = typeof sections[number]['id'];

export function SystemSettings({ catalog, language, readOnly = false, initialSection = 'generation' }: { catalog: Catalog | null; language: Language; readOnly?: boolean; initialSection?: Section }) {
  const t = (text: string) => tr(language, text);
  const [section, setSection] = useState<Section>(initialSection);
  useEffect(() => { setSection(initialSection); }, [initialSection]);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const choices = llmChoices(catalog, language);
  const [selected, setSelected] = useState('');
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [reload, setReload] = useState(0);
  const stale = Boolean(catalog && selected && !choices.some(item => item.value === selected));
  useEffect(() => {
    let alive = true;
    setLoading(true); setError('');
    api.systemSettings().then(value => { if (alive) { setSelected(selectionValue(value.default_llm)); setLoaded(true); } })
      .catch(reason => { if (alive) setError(String(reason.message)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload]);
  return <div className="space-settings-page"><PageHeader icon={Settings2} title={t('空间设置')} description={t('按用途管理默认模型和提示词。')} /><div className="space-settings">
    {readOnly && <p className="space-settings-notice">{t('只读：可使用团队已配置的模型，配置仅限所有者和管理员修改。')}</p>}
    <div className="space-settings-tabs" role="tablist" aria-label={t('空间设置分类')}>
      {sections.map(({ id, label, icon: Icon }, index) => <button key={id} type="button" role="tab" id={`space-tab-${id}`} aria-controls={`space-panel-${id}`} aria-selected={section === id} tabIndex={section === id ? 0 : -1} ref={node => { tabs.current[index] = node; }} onClick={() => setSection(id)} onKeyDown={event => {
        const next = event.key === 'ArrowRight' ? (index + 1) % sections.length : event.key === 'ArrowLeft' ? (index + sections.length - 1) % sections.length : event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1 : -1;
        if (next >= 0) { event.preventDefault(); setSection(sections[next].id); tabs.current[next]?.focus(); }
      }}><Icon aria-hidden="true" />{t(label)}</button>)}
    </div>
    <DatasetSettings catalog={catalog} language={language} readOnly={readOnly} section={section} />
    <section className="panel system-settings-panel" role="tabpanel" id="space-panel-report" aria-labelledby="space-tab-report" hidden={section !== 'report'}>
      <div className="panel-head"><div><h2>{t('AI 测试报告')}</h2><p>{t('用于手动生成运行记录的 AI 测试报告，不改变测试计划中选择的模型。')}</p></div></div>
      <form className="system-settings-form" onSubmit={async event => {
        event.preventDefault(); if (readOnly || !catalog || !loaded || stale) return; setSaving(true); setError(''); setSaved(false);
        try { await api.saveSystemSettings({ default_llm: selected ? JSON.parse(selected) : null }); setSaved(true); }
        catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
        finally { setSaving(false); }
      }}>
        <div className="field"><span className="label-with-tip">{t('默认分析模型')}<InfoTip label={t('查看说明')} text={t('仅可选择模型供应商中已配置的 LLM；使用其现有端点和凭据。保存配置不会调用模型。')}/></span>
          <SearchableSelect label={t('默认分析模型')} language={language} value={selected} disabled={readOnly || !catalog || !loaded || loading || saving} onChange={value => { setSelected(value); setSaved(false); }} options={[{ value: '', label: t('未设置（禁用 AI 分析）') }, ...choices]} />
          {stale && <small className="error-text" role="alert">{t('原默认模型已失效，请重新选择')}</small>}
          {catalog && !choices.length && <small className="field-help">{t('暂无已配置的 LLM，请先在模型供应商中添加。')}</small>}
        </div>
        {(loading || !catalog) && <p className="workspace-muted" role="status">{t('加载中…')}</p>}
        {error && <div className="alert" role="alert">{error}{!loaded && <button type="button" className="ghost compact" onClick={() => setReload(value => value + 1)}>{t('重试')}</button>}</div>}
        <div className="space-settings-footer">{!readOnly && <button className="primary compact" disabled={!catalog || !loaded || loading || saving || stale}><Save aria-hidden="true" />{t(saving ? '保存中…' : '保存配置')}</button>}{saved && <span className="success-text" role="status">{t('空间配置已保存')}</span>}</div>
      </form>
    </section>
  </div></div>;
}
