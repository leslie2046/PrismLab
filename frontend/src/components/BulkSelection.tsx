import { useEffect, useRef, type ReactNode } from 'react';
import type { BulkSelection } from '../bulkSelection';
import { tr, type Language } from '../i18n';

export function SelectionCheckbox({ checked, mixed = false, disabled, label, onChange }: {
  checked: boolean; mixed?: boolean; disabled?: boolean; label: string; onChange: (value: boolean) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (input.current) input.current.indeterminate = mixed; }, [mixed]);
  return <label className="selection-checkbox" onClick={event => event.stopPropagation()}><input ref={input} type="checkbox" aria-label={label} aria-checked={mixed ? 'mixed' : checked} checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} /></label>;
}

export function BulkSelectionBar({ selection, language, children }: {
  selection: BulkSelection; language: Language; children: ReactNode;
}) {
  const t = (value: string) => tr(language, value);
  if (!selection.ids.size && !selection.busy && !selection.error && !selection.result) return null;
  return <div className="bulk-selection" aria-label={t('批量操作')} aria-busy={selection.busy}>
    <div className="bulk-selection-controls"><span className="bulk-count" role="status">{t('已选择')} <strong>{selection.ids.size}</strong></span>
    </div>
    {selection.ids.size > 0 && <div className="bulk-actions">{children}<span className="bulk-scope">{t('仅操作已勾选记录，自动跳过状态不适用的记录。')}</span></div>}
    {selection.busy && <p className="bulk-feedback" role="status">{t('处理中…')}</p>}
    {selection.error && <p className="bulk-feedback error-text" role="alert">{selection.error}</p>}
    {selection.result && <div className="bulk-feedback" role={selection.result.failed.length ? 'alert' : 'status'}>
      <p>{t('操作完成')} · {t('成功')} {selection.result.succeeded.length} · {t('跳过')} {selection.result.skipped.length} · {t('失败')} {selection.result.failed.length}</p>
      {selection.result.failed.length > 0 && <p className="error-text">{t('失败记录仍保留勾选，可重试。')} {selection.result.failed.slice(0, 3).map(value => `${value.id}: ${value.message}`).join(' / ')}</p>}
    </div>}
  </div>;
}
