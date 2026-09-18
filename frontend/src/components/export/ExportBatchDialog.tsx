import { Braces, FileDown, FileSpreadsheet, LoaderCircle, TriangleAlert, X, type LucideIcon } from "lucide-react";
import { Dialog } from "radix-ui";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, toApiError, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { downloadBlob } from "@/lib/export";
import { plural } from "@/lib/format";
import type { BatchFormat, BatchScope } from "@/lib/types";
import { useAudit } from "@/state/audit-store";

interface ExportBatchDialogProps {
  open: boolean;
  onClose: () => void;
  /** Rows ticked in the table that opened the dialog. */
  selectedIds: readonly string[];
}

const FORMATS: { value: BatchFormat; title: string; badge: string; icon: LucideIcon; description: string }[] = [
  {
    value: "accounting_csv",
    title: "QuickBooks / Xero",
    badge: "CSV",
    icon: FileSpreadsheet,
    description: "Bill import layout, one row per line item: InvoiceNumber, VendorName, dates, line quantity, unit price and amount, tax, total, currency.",
  },
  {
    value: "erp_json",
    title: "SAP / NetSuite",
    badge: "JSON",
    icon: Braces,
    description: "Vendor-bill batch with control totals per currency, supplier, lines, approval and each document's SHA-256.",
  },
];

/**
 * Export Batch: the server builds the file from the full ledger (not just what this console has
 * loaded), so "approved" and "everything" cover every invoice in the workspace.
 */
export function ExportBatchDialog({ open, onClose, selectedIds }: ExportBatchDialogProps) {
  const { invoices, archive } = useAudit();
  const [format, setFormat] = useState<BatchFormat>("accounting_csv");
  const [scope, setScope] = useState<BatchScope>("approved");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Each opening starts from the selection, if there is one.
  useEffect(() => {
    if (!open) return;
    setScope(selectedIds.length ? "selected" : "approved");
    setError(null);
  }, [open, selectedIds.length]);

  const byId = useMemo(() => new Map(invoices.map((invoice) => [invoice.invoice_id, invoice])), [invoices]);
  const approvedCount = useMemo(() => invoices.filter((invoice) => invoice.status === "APPROVED").length, [invoices]);
  const selectedUnapproved = selectedIds.filter((id) => byId.get(id)?.status !== "APPROVED").length;
  const archiveComplete = archive.status === "ready" && !archive.truncated;

  const warning =
    scope === "all"
      ? "Includes invoices still awaiting review and rejected ones. Import only approved bills into your ledger, or filter them out on import."
      : scope === "selected" && selectedUnapproved > 0
        ? `${plural(selectedUnapproved, "selected invoice isn't", "selected invoices aren't")} approved. They'll be exported as they are.`
        : null;

  const run = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { blob, filename, count } = await api.exportBatch({
        format,
        scope,
        invoice_ids: scope === "selected" ? [...selectedIds] : undefined,
      });
      downloadBlob(filename, blob);
      toast.success(`Exported ${plural(count, "invoice")}`, { description: filename });
      onClose();
    } catch (failure) {
      setError(toApiError(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && !busy && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay backdrop-blur-[2px] data-[state=open]:animate-fade-in" />
        <Dialog.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 flex max-h-[min(90dvh,760px)] w-[calc(100vw-2rem)] max-w-[600px] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden",
            "rounded-2xl border border-line bg-surface-solid shadow-overlay outline-none data-[state=open]:animate-dialog-in",
          )}
        >
          <header className="flex items-start gap-3.5 px-5 pb-4 pt-5">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-accent/10 text-accent ring-1 ring-inset ring-accent/20" aria-hidden>
              <FileDown className="size-5" strokeWidth={1.75} />
            </span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-[15px] font-semibold tracking-[-0.01em] text-ink">Export batch</Dialog.Title>
              <Dialog.Description className="mt-1 text-[13px] leading-relaxed text-ink-2">
                A file your accounting system can import, built from the full ledger, oldest invoice first.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button size="icon-sm" variant="ghost" aria-label="Close" disabled={busy}>
                <X />
              </Button>
            </Dialog.Close>
          </header>

          <div className="flex min-h-0 flex-col gap-5 overflow-y-auto border-t border-line px-5 py-4">
            <fieldset>
              <legend className="eyebrow">Format</legend>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {FORMATS.map((option) => (
                  <Choice key={option.value} name="batch-format" checked={format === option.value} onSelect={() => setFormat(option.value)} disabled={busy}>
                    <span className="flex items-center gap-2">
                      <option.icon className="size-4 text-ink-3" aria-hidden />
                      <span className="text-[13px] font-medium text-ink">{option.title}</span>
                      <span className="ml-auto rounded-[4px] border border-line px-1 font-mono text-[10.5px] text-ink-3">{option.badge}</span>
                    </span>
                    <span className="mt-1 block text-[12px] leading-snug text-ink-3">{option.description}</span>
                  </Choice>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="eyebrow">Invoices</legend>
              <div className="mt-2 flex flex-col gap-2">
                <Choice name="batch-scope" checked={scope === "approved"} onSelect={() => setScope("approved")} disabled={busy}>
                  <ScopeLabel title="Approved only" count={archiveComplete ? approvedCount : null}>
                    Ready to post: approved by the rule engine or a reviewer.
                  </ScopeLabel>
                </Choice>
                <Choice
                  name="batch-scope"
                  checked={scope === "selected"}
                  onSelect={() => setScope("selected")}
                  disabled={busy || selectedIds.length === 0}
                >
                  <ScopeLabel title="Selected rows" count={selectedIds.length}>
                    {selectedIds.length ? "The rows ticked in the table, whatever their status." : "Tick rows in the table to export just those."}
                  </ScopeLabel>
                </Choice>
                <Choice name="batch-scope" checked={scope === "all"} onSelect={() => setScope("all")} disabled={busy}>
                  <ScopeLabel title="Everything" count={null}>
                    Every audited invoice in this workspace, in any status.
                  </ScopeLabel>
                </Choice>
              </div>
            </fieldset>

            {warning ? (
              <p className="flex items-start gap-2.5 rounded-lg border border-review/30 bg-review/[0.07] px-3 py-2.5 text-[12px] leading-relaxed text-ink-2">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-review" aria-hidden />
                {warning}
              </p>
            ) : null}
            {error ? (
              <p role="alert" className="rounded-lg border border-rejected/30 bg-rejected/[0.06] px-3 py-2.5 text-[12px] leading-relaxed text-ink-2">
                <span className="font-medium text-ink">Couldn't export.</span> {error.message}
              </p>
            ) : null}
          </div>

          <footer className="flex items-center justify-end gap-2 border-t border-line bg-sunken/60 px-5 py-3.5">
            <Dialog.Close asChild>
              <Button variant="ghost" disabled={busy}>
                Cancel
              </Button>
            </Dialog.Close>
            <Button variant="primary" onClick={() => void run()} disabled={busy || (scope === "selected" && selectedIds.length === 0)}>
              {busy ? <LoaderCircle className="animate-spin" aria-hidden /> : <FileDown aria-hidden />}
              {busy ? "Building file…" : `Export ${format === "accounting_csv" ? "CSV" : "JSON"}`}
            </Button>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** A radio button rendered as a card; the native input keeps keyboard and screen-reader behaviour. */
function Choice({
  name,
  checked,
  onSelect,
  disabled,
  children,
}: {
  name: string;
  checked: boolean;
  onSelect: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <label
      className={cn(
        "relative block cursor-pointer rounded-lg border px-3 py-2.5 transition-[border-color,background-color,box-shadow]",
        "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent/70",
        checked ? "border-accent/60 bg-accent/[0.05] shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_14%,transparent)]" : "border-line hover:border-line-strong",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      <input type="radio" name={name} checked={checked} onChange={onSelect} disabled={disabled} className="sr-only" />
      <span className="block">{children}</span>
    </label>
  );
}

function ScopeLabel({ title, count, children }: { title: string; count: number | null; children: ReactNode }) {
  return (
    <>
      <span className="flex items-center gap-2">
        <span className="text-[13px] font-medium text-ink">{title}</span>
        {count !== null ? <span className="figure rounded-full bg-ink-3/10 px-1.5 text-[11px] leading-[18px] text-ink-2">{count.toLocaleString()}</span> : null}
      </span>
      <span className="mt-0.5 block text-[12px] text-ink-3">{children}</span>
    </>
  );
}
