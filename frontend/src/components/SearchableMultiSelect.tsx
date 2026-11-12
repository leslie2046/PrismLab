import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { tr, type Language } from "../i18n";

export function SearchableMultiSelect({ value, onChange, options, label, disabled, language, placeholder, searchPlaceholder, emptyText }: {
  value: string[]; onChange: (value: string[]) => void; options: { value: string; label: string }[];
  label: string; disabled?: boolean; language: Language;
  placeholder?: string; searchPlaceholder?: string; emptyText?: string;
}) {
  const t = (s: string) => tr(language, s);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const filtered = options.filter(item => item.label.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  function choose(next: string) { onChange(value.includes(next) ? value.filter(item => item !== next) : [...value, next]); input.current?.focus(); }
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    const close = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  useEffect(() => { document.getElementById(`${id}-${active}`)?.scrollIntoView({ block: "nearest" }); }, [active, id]);
  return <div className="searchable-select" ref={root} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false); }}>
    <button ref={trigger} type="button" className="searchable-select-trigger" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={`${id}-list`} disabled={disabled} onClick={() => { setOpen(!open); setSearch(""); setActive(0); }} onKeyDown={e => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setOpen(true); setSearch(""); setActive(0); } }}>
      <span>{value.length ? `${t("已选择")} ${value.length} ${t("个知识库")}` : placeholder || t("选择知识库")}</span><ChevronDown aria-hidden="true" />
    </button>
    {open && <div className="searchable-select-popover"><div className="searchable-select-search"><Search aria-hidden="true" /><input ref={input} role="combobox" aria-expanded="true" aria-controls={`${id}-list`} aria-activedescendant={filtered[active] ? `${id}-${active}` : undefined} aria-label={searchPlaceholder || t("搜索知识库")} placeholder={searchPlaceholder || t("搜索知识库")} value={search} onChange={e => { setSearch(e.target.value); setActive(0); }} onKeyDown={e => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); trigger.current?.focus(); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); setActive(current => Math.max(0, Math.min(filtered.length - 1, current + (e.key === "ArrowDown" ? 1 : -1)))); }
      if (e.key === "Enter") { e.preventDefault(); if (filtered[active]) choose(filtered[active].value); }
    }} /></div>
      <div role="listbox" aria-multiselectable="true" aria-label={label} id={`${id}-list`} className="searchable-select-options">{filtered.map((item, index) => <button type="button" role="option" aria-selected={value.includes(item.value)} id={`${id}-${index}`} key={item.value} className={index === active ? "highlighted" : ""} onPointerMove={() => setActive(index)} onClick={() => choose(item.value)}><span>{item.label}</span>{value.includes(item.value) && <Check aria-hidden="true" />}</button>)}</div>
      {!filtered.length && <p className="dify-empty compact" role="status">{emptyText || t("没有匹配的知识库")}</p>}
    </div>}
  </div>;
}
