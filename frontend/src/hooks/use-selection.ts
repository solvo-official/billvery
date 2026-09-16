import { useCallback, useMemo, useState } from "react";

/**
 * A set of selected ids, kept consistent with the ids that currently exist: when a row leaves
 * the list (filtered away stays selected; deleted from the ledger does not) it drops out.
 */
export function useSelection(existing: readonly string[]) {
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());

  const valid = useMemo(() => {
    const present = new Set(existing);
    const kept = [...selected].filter((id) => present.has(id));
    return kept.length === selected.size ? selected : new Set(kept);
  }, [existing, selected]);

  const toggle = useCallback((id: string, on?: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (on ?? !next.has(id)) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  /** Select or clear a batch at once (a page, or every match). */
  const setMany = useCallback((ids: readonly string[], on: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }, []);

  const clear = useCallback(() => setSelected(new Set()), []);

  return { selected: valid, toggle, setMany, clear, has: (id: string) => valid.has(id) };
}
