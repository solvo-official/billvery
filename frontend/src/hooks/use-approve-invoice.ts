import { useCallback } from "react";
import { toast } from "sonner";
import { navigate } from "@/hooks/use-route";
import { api, toApiError } from "@/lib/api";
import { invoiceLabel, plural } from "@/lib/format";
import type { InvoiceRecord, User } from "@/lib/types";
import { useAudit } from "@/state/audit-store";

/** Mirrors the server's default resolution note. */
export const DEFAULT_APPROVAL_NOTE = "Approved and archived.";

/** Approvals in flight, checked synchronously so a double click can't send two. */
const inFlight = new Set<string>();

/** What the server will return, applied locally before it answers. */
export function optimisticApproval(invoice: InvoiceRecord, reviewer: User, note: string | null, at = new Date().toISOString()): InvoiceRecord {
  return {
    ...invoice,
    status: "APPROVED",
    approval: { at, by: { id: reviewer.id, name: reviewer.name }, method: "reviewer" },
    anomalies: invoice.anomalies.map((anomaly) =>
      anomaly.status === "OPEN"
        ? { ...anomaly, status: "DISMISSED" as const, resolved_by: reviewer.id, resolved_at: at, resolution_note: note ?? DEFAULT_APPROVAL_NOTE }
        : anomaly,
    ),
    updated_at: at,
  };
}

export type ApprovalBlocker = "no-reviewer" | "not-in-review" | "pending";

/**
 * Human-in-the-loop "Approve & Archive". The invoice moves to Approved Bills the moment the
 * reviewer confirms; the server's answer then replaces the optimistic copy, or the original
 * comes back (and is re-read from the server) if the approval was refused.
 */
export function useApproveInvoice() {
  const { merge, setPending, pending, reviewer, reviewBlocker } = useAudit();

  const blocker = useCallback(
    (invoice: InvoiceRecord): ApprovalBlocker | null => {
      if (pending[invoice.invoice_id]) return "pending";
      if (invoice.status !== "NEEDS_REVIEW") return "not-in-review";
      if (!reviewer) return "no-reviewer";
      return null;
    },
    [pending, reviewer],
  );

  const approve = useCallback(
    async (invoice: InvoiceRecord, note?: string) => {
      const id = invoice.invoice_id;
      const label = invoiceLabel(invoice);
      if (!reviewer) {
        toast.error(`Can't approve ${label}`, { description: reviewBlocker ?? "No reviewer is available." });
        return;
      }
      if (inFlight.has(id) || invoice.status !== "NEEDS_REVIEW") return;
      inFlight.add(id);

      const trimmed = note?.trim() || null;
      const open = invoice.anomalies.filter((a) => a.status === "OPEN").length;
      const toastId = `approve:${id}`;
      setPending(id, true);
      merge([optimisticApproval(invoice, reviewer, trimmed)], { force: true, highlight: true });
      toast.loading(`Approving ${label}…`, {
        id: toastId,
        description: open ? `Dismissing ${plural(open, "open flag")} as ${reviewer.name}` : `Signing as ${reviewer.name}`,
      });

      try {
        const approved = await api.approve(id, { approved_by: reviewer.id, note: trimmed });
        merge([approved], { force: true, highlight: true });
        toast.success(`${label} successfully approved & archived`, {
          id: toastId,
          description: `Approved just now by ${approved.approval?.by?.name ?? reviewer.name}`,
          action: { label: "View archive", onClick: () => navigate("approved") },
        });
      } catch (error) {
        const failure = toApiError(error);
        // Put the original back, then reconcile with the server: someone else may have acted on it.
        merge([invoice], { force: true, highlight: false });
        let current: InvoiceRecord | null = null;
        try {
          current = await api.audit(id);
          merge([current], { force: true, highlight: false });
        } catch {
          current = null;
        }
        if (failure.status === 409 && current?.status === "APPROVED") {
          toast.info(`${label} was already approved`, {
            id: toastId,
            description: current.approval?.by ? `Approved by ${current.approval.by.name}.` : failure.message,
            action: { label: "View archive", onClick: () => navigate("approved") },
          });
        } else {
          toast.error(`Couldn't approve ${label}`, {
            id: toastId,
            description: failure.unreachable ? `${failure.message} Nothing was changed.` : failure.message,
          });
        }
      } finally {
        inFlight.delete(id);
        setPending(id, false);
      }
    },
    [merge, reviewer, reviewBlocker, setPending],
  );

  return { approve, blocker, reviewer, isPending: (invoiceId: string) => Boolean(pending[invoiceId]) };
}
