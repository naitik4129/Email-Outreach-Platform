import { useCallback, useState } from "react";

// The set of selected row ids for bulk actions. Callers clear it when the list
// they are looking at changes (filter, page), so a hidden row is never acted on.
export function useSelection() {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const toggle = useCallback((id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const setAll = useCallback((ids: string[]) => setSelected(new Set(ids)), []);
  // A no-op when already empty, so calling it from an effect never re-renders.
  const clear = useCallback(
    () => setSelected((current) => (current.size === 0 ? current : new Set())),
    [],
  );

  return { selected, toggle, setAll, clear, count: selected.size };
}
