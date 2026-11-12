export function CompactSwitch({checked, disabled, busy, label, title, onChange}: {
  checked:boolean; disabled?:boolean; busy?:boolean; label:string; title:string; onChange:()=>void;
}) {
  return <button type="button" role="switch" className="compact-switch" aria-checked={checked}
    aria-label={label} aria-busy={busy || undefined} disabled={disabled} title={title} onClick={onChange}>
    <span className="compact-switch-track" aria-hidden="true"><span /></span>
  </button>;
}
