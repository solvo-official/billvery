import { Braces, FileSearch, FileSpreadsheet, X } from "lucide-react";
import { Kbd } from "@/components/audit/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { formatAmount } from "@/lib/money";
import type { ExportFormat } from "@/lib/export";
import type { InvoiceRecord } from "@/lib/types";
import { totalsByCurrency } from "./archive";

interface BulkActionBarProps {
  selected: InvoiceRecord[];
  /** bills matching the current filters */
  matching: number;
  onSelectAllMatching: () => void;
  onClear: () => void;
  onExport: (format: ExportFormat) => void;
  onReview: () => void;
}

/** Floats over the table while bills are selected: running totals, exports and a report walk-through. */
export function BulkActionBar({ selected, matching, onSelectAllMatching, onClear, onExport, onReview }: BulkActionBarProps) {
  const totals = totalsByCurrency(selected);
  const shown = totals.slice(0, 2);

  return (
    <div
      role="toolbar"
      aria-label={`${selected.length} bills selected`}
      className="pointer-events-none fixed inset-x-0 bottom-5 z-40 flex justify-center px-4"
    >
      <div className="glass pointer-events-auto flex max-w-full animate-rise-in flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl py-2 pl-4 pr-2 shadow-overlay [background-color:color-mix(in_oklab,var(--surface-solid)_86%,transparent)]">
        <div className="flex items-center gap-2 text-[13px]">
          <span className="figure inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-accent px-1.5 text-[12px] font-medium text-on-accent">
            {selected.length}
          </span>
          <span className="text-ink">selected</span>
          {selected.length < matching ? (
            <button type="button" onClick={onSelectAllMatching} className="cursor-pointer text-[12px] font-medium text-accent hover:underline">
              Select all {matching.toLocaleString()}
            </button>
          ) : null}
        </div>

        {shown.length > 0 ? (
          <>
            <span className="hidden h-5 w-px bg-line sm:block" aria-hidden />
            <p className="figure hidden items-center gap-3 text-[12px] text-ink-2 sm:flex" aria-label="Selected totals">
              {shown.map((total) => (
                <span key={total.currency}>
                  <span className="mr-1 text-[11px] text-ink-3">{total.currency}</span>
                  {formatAmount(total.amount, total.currency)}
                </span>
              ))}
              {totals.length > shown.length ? <span className="font-sans text-ink-3">+{totals.length - shown.length} more</span> : null}
            </p>
          </>
        ) : null}

        <span className="hidden h-5 w-px bg-line sm:block" aria-hidden />
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="sm" variant="ghost" onClick={onReview}>
            <FileSearch aria-hidden /> Review reports
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onExport("csv")}>
            <FileSpreadsheet aria-hidden /> Export CSV
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onExport("json")}>
            <Braces aria-hidden /> Export JSON
          </Button>
          <Tooltip content={<span>Clear selection <Kbd>Esc</Kbd></span>}>
            <Button size="icon-sm" variant="ghost" onClick={onClear} aria-label="Clear selection">
              <X />
            </Button>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
