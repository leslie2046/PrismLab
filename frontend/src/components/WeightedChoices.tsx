import { useId, useRef } from 'react';
import { tr, type Language } from '../i18n';
import { InfoTip } from './InfoTip';

export function WeightedChoices({ weights, options, heading, disabled, inactive = false, language, onChange }: {
  weights: Record<string, number>; options: { key: string; label: string; help: string; unavailable?: boolean }[];
  heading: string; disabled: boolean; inactive?: boolean; language: Language; onChange: (weights: Record<string, number>) => void;
}) {
  const t = (text: string) => tr(language, text);
  const id = useId();
  const remembered = useRef<Record<string, number>>({});
  const total = options.reduce((sum, option) => sum + (option.unavailable ? 0 : weights[option.key] || 0), 0);
  function update(key: string, weight: number) {
    if (weights[key] > 0) remembered.current[key] = weights[key];
    onChange({ ...weights, [key]: weight });
  }
  return <>
    <div className="dataset-evolution-columns" aria-hidden="true"><span>{heading}</span><span>{t('相对权重')}</span><span>{t('抽样占比')}</span></div>
    {options.map(({ key, label, help, unavailable }) => {
      const weight = weights[key] || 0;
      const percent = !unavailable && total > 0 ? weight / total * 100 : 0;
      const format = new Intl.NumberFormat(language, { maximumFractionDigits: 1 });
      const percentText = (percent > 0 && percent < 0.1 ? '<' + format.format(0.1) : format.format(percent)) + '%';
      const status = unavailable ? t('不适用') : inactive ? t('未执行') : weight > 0 ? percentText : t('已关闭');
      return <div className="dataset-evolution-row" key={key}>
        <div className="label-with-tip dataset-evolution-name"><label htmlFor={`${id}-${key}-enabled`}><input id={`${id}-${key}-enabled`} type="checkbox" checked={weight > 0} disabled={disabled || inactive || unavailable} onChange={event => update(key, event.target.checked ? remembered.current[key] || 1 : 0)} /><span>{label}</span></label><InfoTip label={`${label} · ${t('查看说明')}`} id={`${id}-${key}-help`} text={help} /></div>
        <input className="dataset-evolution-weight" type="number" inputMode="decimal" min={0} step="any" aria-label={`${label} · ${t('相对权重')}`} aria-describedby={`${id}-${key}-help`} value={weight} disabled={disabled || inactive || unavailable} onChange={event => { const next = Number(event.target.value); if (Number.isFinite(next)) update(key, Math.max(0, next)); }} />
        <div className="dataset-evolution-share"><output aria-label={`${label} · ${t('抽样占比')}`}>{status}</output><span className="dataset-evolution-meter" aria-hidden="true"><span style={{ width: `${inactive ? 0 : percent}%` }} /></span></div>
      </div>;
    })}
  </>;
}
