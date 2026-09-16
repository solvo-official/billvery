import { Archive, ChevronDown, ChevronUp, Plug, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ApprovalStamp } from "@/components/audit/approval";
import { Kbd, RiskMeter, StatusBadge } from "@/components/audit/status";
import { Button } from "@/components/ui/button";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tooltip } from "@/components/ui/tooltip";
import { isTypingTarget } from "@/hooks/use-hotkey";
import { useNow } from "@/hooks/use-now";
import { api, toApiError, type ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/dates";
import { anomalyFields, bySeverity, decideInvoiceStatus, riskScore, SEVERITY_RANK } from "@/lib/rules";
import type { DocField, InvoiceRecord, Resolution, Severity } from "@/lib/types";
import { useAudit, useOrganization } from "@/state/audit-store";
import { AnomalyCard } from "./AnomalyCard";
import { DocumentViewer } from "./DocumentViewer";
import { ExtractedFields } from "./ExtractedFields";
import { MIN_NOTE_LENGTH, ResolutionPanel, type CommitReceipt } from "./ResolutionPanel";

interface DrawerProps {
  invoiceId: string | null;
  onClose: () => void;
  onNavigate: (invoiceId: string) => void;
  /** Opens the Approve & Archive confirmation for this invoice. */
  onApprove: (invoiceId: string) => void;
}

export function AnomalyInspectionDrawer({ invoiceId, onClose, onNavigate, onApprove }: DrawerProps) {
  const { invoices } = useAudit();
  const invoice = invoiceId ? invoices.find((i) => i.invoice_id === invoiceId) ?? null : null;
  const inspectable = invoice !== null && invoice.status !== "PROCESSING";

  return (
    <Sheet open={inspectable} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="max-w-[1340px] lg:w-[94vw]">
        {inspectable ? (
          <ErrorBoundary label="This invoice" resetKeys={[invoice.invoice_id]} className="m-5">
            <InspectionBody key={invoice.invoice_id} invoice={invoice} onNavigate={onNavigate} onApprove={onApprove} />
          </ErrorBoundary>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

const SOURCE_ICON = { api: Plug, upload: Upload } as const;

function InspectionBody({
  invoice,
  onNavigate,
  onApprove,
}: {
  invoice: InvoiceRecord;
  onNavigate: (invoiceId: string) => void;
  onApprove: (invoiceId: string) => void;
}) {
  const { invoices, merge, reviewer, userName, pending, reviewBlocker } = useAudit();
  const organization = useOrganization();
  const now = useNow(30_000);
  const [staged, setStaged] = useState<Record<string, Resolution>>({});
  const [note, setNote] = useState("");
  const [pointedId, setPointedId] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const [receipt, setReceipt] = useState<CommitReceipt | null>(null);
  const saving = Boolean(pending[invoice.invoice_id]);

  // Approved while open (here, through the dialog, or elsewhere): keep the queue controls so
  // the reviewer can move straight on to the next invoice.
  const [approvedHere, setApprovedHere] = useState(false);
  const previousStatus = useRef(invoice.status);
  useEffect(() => {
    if (previousStatus.current === "NEEDS_REVIEW" && invoice.status === "APPROVED") {
      setApprovedHere(true);
      setStaged({});
    }
    previousStatus.current = invoice.status;
  }, [invoice.status]);

  // The feed copy may be a poll old: fetch the invoice fresh when it is opened.
  useEffect(() => {
    const controller = new AbortController();
    api.audit(invoice.invoice_id, controller.signal).then(
      (fresh) => merge([fresh], { highlight: false }),
      () => undefined, // keep showing the feed copy; the next poll refreshes it
    );
    return () => controller.abort();
  }, [invoice.invoice_id, merge]);

  const anomalies = useMemo(
    () => [...invoice.anomalies].sort((a, b) => Number(b.status === "OPEN") - Number(a.status === "OPEN") || bySeverity(a, b)),
    [invoice.anomalies],
  );
  const open = anomalies.filter((a) => a.status === "OPEN");
  const decided = open.filter((a) => staged[a.id]);
  const outcome = decideInvoiceStatus(
    invoice.anomalies.map((a) => ({ severity: a.severity, status: a.status === "OPEN" ? staged[a.id] ?? "OPEN" : a.status })),
  );

  // The review queue, highest risk first. Once this invoice leaves the queue (after a commit),
  // "next" is simply the highest-risk invoice still waiting.
  const queue = useMemo(
    () =>
      invoices
        .filter((i) => i.status === "NEEDS_REVIEW")
        .map((i) => ({ id: i.invoice_id, label: i.invoice_number ?? "invoice", risk: riskScore(i) ?? 0, created: i.created_at }))
        .sort((a, b) => b.risk - a.risk || a.created.localeCompare(b.created)),
    [invoices],
  );
  const position = queue.findIndex((item) => item.id === invoice.invoice_id);
  const inQueue = position >= 0 || receipt !== null || approvedHere;
  const stagedConfirmations = decided.filter((a) => staged[a.id] === "CONFIRMED").length;
  const approveBlocker = !reviewer
    ? reviewBlocker
    : committing
      ? "Wait for the commit to finish."
      : stagedConfirmations > 0
        ? "You've staged a confirmation. Approving dismisses every open flag, so clear it or commit it first."
        : null;
  const previous = position > 0 ? queue[position - 1] : undefined;
  const next = position >= 0 ? queue[position + 1] : queue[0];

  useEffect(() => {
    if (!inQueue) return;
    const onKey = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "j" && next) onNavigate(next.id);
      if (event.key === "k" && previous) onNavigate(previous.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [inQueue, next, previous, onNavigate]);

  // Regions on the page tied to findings still in play, strongest severity wins.
  const flagged = useMemo(() => {
    const map = new Map<DocField, Severity>();
    for (const anomaly of invoice.anomalies) {
      if (anomaly.status === "DISMISSED") continue;
      for (const field of anomalyFields(anomaly, invoice)) {
        const current = map.get(field);
        if (!current || SEVERITY_RANK[anomaly.severity] > SEVERITY_RANK[current]) map.set(field, anomaly.severity);
      }
    }
    return map;
  }, [invoice]);
  const pointed = invoice.anomalies.find((a) => a.id === pointedId);
  const emphasized = pointed ? anomalyFields(pointed, invoice) : [];

  const spikedLines = useMemo(
    () =>
      new Map(
        invoice.anomalies
          .filter((a) => a.type === "PRICE_SPIKE" && a.status !== "DISMISSED")
          .map((a) => [Number(a.detected_value?.line_number), Number(a.detected_value?.ratio)] as const),
      ),
    [invoice.anomalies],
  );

  /** Open another invoice, fetching it first when it is older than the loaded history. */
  const openInvoice = useCallback(
    async (invoiceId: string) => {
      if (!invoices.some((i) => i.invoice_id === invoiceId)) {
        try {
          merge([await api.audit(invoiceId)], { highlight: false });
        } catch (error) {
          toast.error("Couldn't open that invoice", { description: toApiError(error).message });
          return;
        }
      }
      onNavigate(invoiceId);
    },
    [invoices, merge, onNavigate],
  );

  const commit = async () => {
    const trimmed = note.trim();
    if (committing || !reviewer || decided.length === 0 || trimmed.length < MIN_NOTE_LENGTH) return;
    setCommitting(true);
    const decisions = decided.map((a) => ({ anomalyId: a.id, resolution: staged[a.id]! }));
    let committed = 0;
    let failure: ApiError | null = null;
    // One resolve call per finding, in order; stop at the first refusal.
    for (const decision of decisions) {
      try {
        await api.resolve(decision.anomalyId, { resolution: decision.resolution, resolved_by: reviewer.id, note: trimmed });
        committed += 1;
      } catch (error) {
        failure = toApiError(error);
        break;
      }
    }

    // Re-read the invoice: the server's status and resolutions are the record.
    let fresh: InvoiceRecord | null = null;
    try {
      fresh = await api.audit(invoice.invoice_id);
      merge([fresh]);
    } catch {
      fresh = null;
    }
    const current = fresh ?? invoice;
    setStaged((stagedNow) =>
      Object.fromEntries(Object.entries(stagedNow).filter(([id]) => current.anomalies.some((a) => a.id === id && a.status === "OPEN"))),
    );
    if (committed > 0) setReceipt({ decisions: committed, status: current.status });

    const label = invoice.invoice_number ?? "invoice";
    if (!failure) {
      setNote("");
      toast.success(`Audit committed · ${label}`, {
        description:
          current.status === "REJECTED"
            ? "Rejected. The confirmed finding is in the audit log."
            : current.status === "APPROVED"
              ? "Approved for payment."
              : "Still in review: a blocking flag remains open.",
      });
    } else {
      toast.error(committed > 0 ? `Committed ${committed} of ${decisions.length} decisions on ${label}` : `Couldn't commit the audit on ${label}`, {
        description: failure.message,
      });
    }
    setCommitting(false);
  };

  const SourceIcon = SOURCE_ICON[invoice.source];
  const canOpen = (id: string) => id !== invoice.invoice_id;

  return (
    <>
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3 sm:px-5">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <SheetClose asChild>
            <Button size="icon-sm" variant="ghost" aria-label="Close inspection">
              <X />
            </Button>
          </SheetClose>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <SheetTitle className="font-mono text-[18px] font-medium leading-tight tracking-[-0.01em] text-ink">
                {invoice.invoice_number ?? "Invoice"}
              </SheetTitle>
              <StatusBadge status={invoice.status} openFlags={open.length} />
              {invoice.approval ? <ApprovalStamp approval={invoice.approval} now={now} pending={saving} /> : null}
            </div>
            <SheetDescription className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 text-[12px] text-ink-3">
              <span className="truncate text-ink-2">{invoice.vendor.name ?? "Unknown vendor"}</span>
              <span aria-hidden>·</span>
              <SourceIcon className="size-3" aria-hidden />
              <span>
                received {relativeTime(invoice.created_at, now)}{" "}
                {invoice.submitted_by ? `from ${invoice.submitted_by.name}` : "through the API"}
              </span>
              <span aria-hidden>·</span>
              <Tooltip content={<span className="figure break-all">sha256 {invoice.document.sha256}</span>}>
                <span className="figure cursor-default" tabIndex={0}>
                  {invoice.document.sha256.slice(0, 8)}
                </span>
              </Tooltip>
            </SheetDescription>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="eyebrow">Risk</span>
            <RiskMeter score={riskScore(invoice)} />
          </div>
          {inQueue ? (
            <div className="flex items-center gap-1">
              <span className="figure mr-1 text-[12px] text-ink-3">
                {position >= 0 ? `${position + 1} / ${queue.length}` : `${queue.length} waiting`}
              </span>
              <Tooltip content={<span>Previous in queue <Kbd>K</Kbd></span>}>
                <Button size="icon-sm" variant="outline" aria-label="Previous invoice in queue" disabled={!previous} onClick={() => previous && onNavigate(previous.id)}>
                  <ChevronUp />
                </Button>
              </Tooltip>
              <Tooltip content={<span>Next in queue <Kbd>J</Kbd></span>}>
                <Button size="icon-sm" variant="outline" aria-label="Next invoice in queue" disabled={!next} onClick={() => next && onNavigate(next.id)}>
                  <ChevronDown />
                </Button>
              </Tooltip>
            </div>
          ) : null}
          {invoice.status === "NEEDS_REVIEW" ? (
            <Tooltip content={approveBlocker ?? `Dismiss ${open.length === 1 ? "the open flag" : `all ${open.length} open flags`} and move this invoice to Approved Bills`}>
              <span>
                <Button variant="approve" disabled={approveBlocker !== null} onClick={() => onApprove(invoice.invoice_id)}>
                  <Archive aria-hidden /> Approve &amp; Archive
                </Button>
              </span>
            </Tooltip>
          ) : null}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1.02fr)_minmax(420px,1fr)] lg:grid-rows-[minmax(0,1fr)] lg:overflow-hidden">
        <div className="flex h-[62vh] min-h-0 flex-col border-b border-line lg:h-auto lg:border-b-0 lg:border-r">
          <DocumentViewer invoice={invoice} organization={organization} flagged={flagged} emphasized={emphasized} />
        </div>

        <div className="flex min-h-0 flex-col">
          <div className="flex-1 space-y-6 px-4 py-4 sm:px-5 lg:overflow-y-auto">
            <ExtractedFields invoice={invoice} spikedLines={spikedLines} />

            <section aria-labelledby="flags-heading" className="flex flex-col gap-3">
              <div className="flex items-baseline justify-between gap-3">
                <h3 id="flags-heading" className="eyebrow">
                  Flagged anomalies
                </h3>
                <span className="text-[11px] text-ink-3">
                  {open.length} open · {invoice.anomalies.length - open.length} resolved
                </span>
              </div>
              {anomalies.length === 0 ? (
                <p className="rounded-lg border border-line px-3.5 py-4 text-[12px] text-ink-3">
                  No rule flagged this invoice. It was approved without review.
                </p>
              ) : (
                anomalies.map((anomaly) => (
                  <AnomalyCard
                    key={anomaly.id}
                    anomaly={anomaly}
                    invoice={invoice}
                    staged={staged[anomaly.id]}
                    pointed={pointedId === anomaly.id}
                    disabled={committing || !reviewer}
                    onStage={(resolution) =>
                      setStaged((current) => {
                        const nextStaged = { ...current };
                        if (resolution) nextStaged[anomaly.id] = resolution;
                        else delete nextStaged[anomaly.id];
                        return nextStaged;
                      })
                    }
                    onPoint={(active) => setPointedId((current) => (active ? anomaly.id : current === anomaly.id ? null : current))}
                    userName={userName}
                    onOpenInvoice={(id) => void openInvoice(id)}
                    canOpen={canOpen}
                  />
                ))
              )}
            </section>
          </div>

          <ResolutionPanel
            openCount={open.length}
            decidedCount={decided.length}
            outcome={outcome}
            note={note}
            onNoteChange={setNote}
            onCommit={() => void commit()}
            onClear={() => setStaged({})}
            committing={committing}
            reviewer={reviewer}
            reviewBlocker={reviewBlocker}
            receipt={receipt}
            approval={invoice.approval}
            approvedHere={approvedHere}
            saving={saving}
            now={now}
            nextInQueue={next ? { label: next.label, onGo: () => onNavigate(next.id) } : null}
          />
        </div>
      </div>
    </>
  );
}
