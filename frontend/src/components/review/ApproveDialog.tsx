import { Archive, ShieldAlert, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Avatar, Kbd, Money, SeverityTag, StatusBadge } from "@/components/audit/status";
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { isMac } from "@/hooks/use-hotkey";
import { DEFAULT_APPROVAL_NOTE, useApproveInvoice } from "@/hooks/use-approve-invoice";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/dates";
import { invoiceLabel, plural } from "@/lib/format";
import { ANOMALY_INFO, bySeverity } from "@/lib/rules";
import { useAudit } from "@/state/audit-store";

/**
 * The human-in-the-loop checkpoint before an invoice is approved: lists every open flag that
 * approval will dismiss, calls out fraud signals, takes an optional note and names the signer.
 */
export function ApproveDialog({ invoiceId, onClose }: { invoiceId: string | null; onClose: () => void }) {
  const { invoices, reviewBlocker } = useAudit();
  const invoice = invoiceId ? invoices.find((item) => item.invoice_id === invoiceId) ?? null : null;
  const { approve, reviewer } = useApproveInvoice();
  const [note, setNote] = useState("");

  useEffect(() => setNote(""), [invoiceId]);

  const open = invoice?.anomalies.filter((a) => a.status === "OPEN").sort(bySeverity) ?? [];
  const fraud = open.filter((a) => ANOMALY_INFO[a.type].kind === "fraud");
  const stale = invoice !== null && invoice.status !== "NEEDS_REVIEW";
  const ready = invoice !== null && !stale && reviewer !== null;

  const confirm = () => {
    if (!ready) return;
    void approve(invoice, note);
    onClose();
  };

  return (
    <AlertDialog open={invoice !== null} onOpenChange={(next) => !next && onClose()}>
      {invoice ? (
        <AlertDialogContent aria-describedby="approve-summary">
          <div className="flex items-start gap-3.5 px-5 pb-4 pt-5">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-approved/10 text-approved ring-1 ring-inset ring-approved/20" aria-hidden>
              <ShieldCheck className="size-5" strokeWidth={1.75} />
            </span>
            <div className="min-w-0 flex-1">
              <AlertDialogTitle>Approve &amp; archive {invoiceLabel(invoice)}?</AlertDialogTitle>
              <AlertDialogDescription id="approve-summary" className="mt-1 flex flex-wrap items-center gap-x-1.5">
                <span className="truncate text-ink">{invoice.vendor.name ?? "Unknown vendor"}</span>
                <span aria-hidden>·</span>
                <Money amount={invoice.financial_summary.total} currency={invoice.financial_summary.currency} className="text-ink" />
                {invoice.invoice_date ? (
                  <>
                    <span aria-hidden>·</span>
                    <span>issued {formatDate(invoice.invoice_date)}</span>
                  </>
                ) : null}
              </AlertDialogDescription>
            </div>
          </div>

          <div className="flex min-h-0 flex-col gap-4 overflow-y-auto border-t border-line px-5 py-4">
            {stale ? (
              <p className="flex items-center gap-2 rounded-lg border border-line bg-sunken px-3 py-2.5 text-[12px] text-ink-2">
                This invoice is no longer waiting for review. It's now <StatusBadge status={invoice.status} />
              </p>
            ) : open.length === 0 ? (
              <p className="text-[13px] text-ink-2">No flags are open. The invoice will be approved as it stands.</p>
            ) : (
              <section aria-labelledby="dismiss-heading">
                <h3 id="dismiss-heading" className="eyebrow">
                  {plural(open.length, "open flag")} will be dismissed
                </h3>
                <ul className="mt-2 divide-y divide-line overflow-hidden rounded-lg border border-line">
                  {open.map((anomaly) => {
                    const info = ANOMALY_INFO[anomaly.type];
                    return (
                      <li key={anomaly.id} className="flex items-center gap-3 bg-surface-solid px-3 py-2">
                        <SeverityTag severity={anomaly.severity} className="w-[108px] shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{info.label}</span>
                        {info.kind === "fraud" ? (
                          <span className="shrink-0 rounded-full bg-rejected/10 px-2 py-0.5 text-[11px] font-medium text-rejected ring-1 ring-inset ring-rejected/20">
                            Fraud signal
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}

            {fraud.length > 0 && !stale ? (
              <div className="flex items-start gap-2.5 rounded-lg border border-rejected/25 bg-rejected/[0.06] px-3 py-2.5">
                <ShieldAlert className="mt-0.5 size-4 shrink-0 text-rejected" aria-hidden />
                <p className="text-[12px] leading-relaxed text-ink-2">
                  <span className="font-medium text-ink">{plural(fraud.length, "fraud signal")} will be overridden.</span> Confirm the invoice
                  with the vendor using contact details from your vendor master, not ones printed on the document, before approving.
                </p>
              </div>
            ) : null}

            <div>
              <label htmlFor="approval-note" className="eyebrow flex items-baseline justify-between">
                Approval note <span className="font-sans text-[11px] font-normal normal-case tracking-normal text-ink-3">Optional</span>
              </label>
              <textarea
                id="approval-note"
                rows={2}
                value={note}
                maxLength={2000}
                disabled={!ready}
                onChange={(event) => setNote(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                    event.preventDefault();
                    confirm();
                  }
                }}
                placeholder={`What did you verify? Recorded on each dismissed flag; defaults to “${DEFAULT_APPROVAL_NOTE}”`}
                className={cn(
                  "mt-1.5 w-full resize-none rounded-lg border border-line bg-surface-solid px-3 py-2 text-[13px] leading-snug text-ink placeholder:text-ink-3",
                  "transition-[border-color,box-shadow] focus:border-accent/60 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_18%,transparent)] focus:outline-none disabled:opacity-60",
                )}
              />
            </div>
          </div>

          <footer className="flex flex-wrap items-center gap-3 border-t border-line bg-sunken/60 px-5 py-3.5">
            {reviewer ? (
              <span className="flex min-w-0 items-center gap-2 text-[12px] text-ink-3">
                <Avatar name={reviewer.name} className="size-5 text-[9.5px]" />
                <span className="truncate">
                  Signing as <span className="font-medium text-ink-2">{reviewer.name}</span>
                </span>
              </span>
            ) : (
              <span className="text-[12px] text-review">{reviewBlocker}</span>
            )}
            <div className="ml-auto flex items-center gap-2">
              <AlertDialogCancel asChild>
                <Button variant="ghost">Cancel</Button>
              </AlertDialogCancel>
              <Button variant="approve" disabled={!ready} onClick={confirm}>
                <Archive aria-hidden />
                Approve &amp; Archive
                <Kbd className="ml-1 border-white/20 bg-black/10 text-current opacity-80">{isMac ? "⌘↵" : "Ctrl ↵"}</Kbd>
              </Button>
            </div>
          </footer>
        </AlertDialogContent>
      ) : null}
    </AlertDialog>
  );
}
