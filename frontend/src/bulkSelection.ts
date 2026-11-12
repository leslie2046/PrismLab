import { useEffect, useRef, useState } from 'react';

export type SelectionSnapshot = { ids: string[]; total: number };
export type BulkResult = { succeeded: string[]; skipped: string[]; failed: { id: string; message: string }[] };
const empty = (key: string) => ({ key, ids: new Set<string>(), busy: false, error: '', result: null as BulkResult | null });

export function useBulkSelection(key: string) {
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const currentKey = useRef({ key, revision: 0 });
  if (currentKey.current.key !== key) currentKey.current = { key, revision: currentKey.current.revision + 1 };
  const scope = currentKey.current;
  const [saved, setSaved] = useState(() => empty(key));
  if (saved.key !== key) setSaved(empty(key));
  const state = saved.key === key ? saved : empty(key);
  function update(change: (value: typeof saved) => typeof saved) {
    if (mounted.current && currentKey.current === scope) setSaved(previous => change(previous.key === key ? previous : empty(key)));
  }
  function toggle(ids: string[], selected: boolean) {
    update(previous => {
      const next = new Set(previous.ids);
      ids.forEach(id => selected ? next.add(id) : next.delete(id));
      return { ...previous, ids: next, result: null, error: '' };
    });
  }
  async function selectAll(load: () => Promise<SelectionSnapshot>) {
    update(previous => ({ ...previous, busy: true, error: '', result: null }));
    try { const value = await load(); update(previous => ({ ...previous, ids: new Set(value.ids) })); }
    catch (reason) { update(previous => ({ ...previous, error: reason instanceof Error ? reason.message : String(reason) })); }
    finally { update(previous => ({ ...previous, busy: false })); }
  }
  async function execute(run: (ids: string[]) => Promise<BulkResult>, changed: (value: BulkResult) => void) {
    if (!state.ids.size || state.busy) return;
    update(previous => ({ ...previous, busy: true, error: '', result: null }));
    try {
      const result = await run([...state.ids]);
      const succeeded = new Set(result.succeeded);
      update(previous => ({ ...previous, result, ids: new Set([...previous.ids].filter(id => !succeeded.has(id))) }));
      if (mounted.current) changed(result);
    } catch (reason) { update(previous => ({ ...previous, error: reason instanceof Error ? reason.message : String(reason) })); }
    finally { update(previous => ({ ...previous, busy: false })); }
  }
  return { ...state, toggle, selectAll, execute, clear: () => update(() => empty(key)) };
}
export type BulkSelection = ReturnType<typeof useBulkSelection>;
