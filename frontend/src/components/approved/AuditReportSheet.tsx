import {
  Bot,
  Braces,
  Check,
  ChevronDown,
  ChevronUp,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  Copy,
  Download,
  ExternalLink,
  FileInput,
  FileSpreadsheet,
  ScanText,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  TriangleAlert,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { approvalSentence } from "@/components/audit/approval";
import { Avatar, Kbd, Money, SeverityTag, StatusBadge } from "@/components/audit/status";
import { ExtractedFields } from "@/components/inspection/ExtractedFields";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ErrorBoundary, PanelError } from "@/components/ui/error-boundary";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { LoadingLabel, Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { useHotkey } from "@/hooks/use-hotkey";
import { useNow } from "@/hooks/use-now";
import { api, toApiError, type ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/dates";
import type { ExportFormat } from "@/lib/export";
import { formatBytes, plural } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { ANOMALY_INFO, bySeverity } from "@/lib/rules";
import type { Anomaly, InvoiceRecord } from "@/lib/types";
import { useAudit } from "@/state/audit-store";
import { buildChecks, buildTrail, effectiveTaxRate, type Check as VerificationCheck, type CheckState, type TrailEvent } from "./report";

interface AuditReportSheetProps {
  invoiceId: string | null;
  /** When stepping through a selection: the invoice ids in table order. */
  sequence: string[] | null;
  onNavigate: (invoiceId: string) => void;
  onClose: () => void;
  onExport: (invoice: InvoiceRecord, format: ExportFormat) => void;
}

/** The evidence file for one approved bill: approval trail, verification checks, figures, flags and the document. */
export function AuditReportSheet({ invoiceId, sequence, onNavigate, onClose, onExport }: AuditReportSheetProps) {
  const { invoices } = useAudit();
  const invoice = invoiceId ? invoices.find((item) => item.invoice_id === invoiceId) ?? null : null;

  return (
    <Sheet open={invoiceId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="max-w-[780px] sm:w-[92vw]" aria-describedby={undefined}>
        {invoiceId ? (
          <ErrorBoundary label="This audit report" resetKeys={[invoiceId]} className="m-5">
            {invoice ? (
              <ReportBody key={invoice.invoice_id} invoice={invoice} sequence={sequence} onNavigate={onNavigate} onExport={onExport} />
            ) : (
              <ReportLoader key={invoiceId} invoiceId={invoiceId} />
            )}
          </ErrorBoundary>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

/** The invoice isn't in the ledger (an old link): fetch it, with a skeleton in the report's shape. */
function ReportLoader({ invoiceId }: { invoiceId: string }) {
  const { merge } = useAudit();
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.audit(invoiceId, controller.signal).then(
      (fresh) => merge([fresh], { highlight: false }),
      (failure: unknown) => {
        const apiError = toApiError(failure);
        if (!apiError.aborted) setError(apiError);
      },
    );
    return () => controller.abort();
  }, [invoiceId, merge, attempt]);

  if (error) {
    return (
      <div className="p-5">
        <SheetTitle className="sr-only">Audit report unavailable</SheetTitle>
        <PanelError label="This audit report" error={error} onRetry={() => setAttempt((count) => count + 1)} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-6 p-5">
      <SheetTitle className="sr-only">Loading audit report</SheetTitle>
      <LoadingLabel>Loading the audit report…</LoadingLabel>
      <div>
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-6 w-48" />
        <Skeleton className="mt-2 h-3.5 w-64" />
      </div>
      <Skeleton className="h-24 w-full rounded-xl" />
      {Array.from({ length: 5 }, (_, index) => (
        <Skeleton key={index} className="h-10 w-full rounded-lg" />
      ))}
    </div>
  );
}

function ReportBody({
  invoice,
  sequence,
  onNavigate,
  onExport,
}: {
  invoice: InvoiceRecord;
  sequence: string[] | null;
  onNavigate: (invoiceId: string) => void;
  onExport: (invoice: InvoiceRecord, format: ExportFormat) => void;
}) {
  const { merge, userName, pending } = useAudit();
  const now = useNow(30_000);
  const [refreshing, setRefreshing] = useState(true);

  // The ledger copy may be a poll old: re-read the audit when the report opens.
  useEffect(() => {
    const controller = new AbortController();
    api.audit(invoice.invoice_id, controller.signal).then(
      (fresh) => {
        merge([fresh], { highlight: false });
        setRefreshing(false);
      },
      () => {
        if (!controller.signal.aborted) setRefreshing(false);
      },
    );
    return () => controller.abort();
  }, [invoice.invoice_id, merge]);

  const position = sequence ? sequence.indexOf(invoice.invoice_id) : -1;
  const previous = position > 0 ? sequence![position - 1] : undefined;
  const next = position >= 0 && sequence ? sequence[position + 1] : undefined;
  useHotkey("j", () => next && onNavigate(next), { enabled: Boolean(next), allowInDialog: true });
  useHotkey("k", () => previous && onNavigate(previous), { enabled: Boolean(previous), allowInDialog: true });

  const checks = useMemo(() => buildChecks(invoice, userName), [invoice, userName]);
  const trail = useMemo(() => buildTrail(invoice, userName), [invoice, userName]);
  const spikedLines = useMemo(
    () =>
      new Map(
        invoice.anomalies
          .filter((a) => a.type === "PRICE_SPIKE" && a.status !== "DISMISSED")
          .map((a) => [Number(a.detected_value?.line_number), Number(a.detected_value?.ratio)] as const),
      ),
    [invoice.anomalies],
  );
  const saving = Boolean(pending[invoice.invoice_id]);
  const passed = checks.filter((check) => check.state === "passed").length;
  const ran = checks.filter((check) => check.state !== "not-run").length;

  return (
    <>
      <header className="flex flex-wrap items-start gap-x-4 gap-y-3 border-b border-line px-5 pb-4 pt-4">
        <SheetClose asChild>
          <Button size="icon-sm" variant="ghost" aria-label="Close audit report" className="-ml-1.5">
            <X />
          </Button>
        </SheetClose>
        <div className="min-w-0 flex-1">
          <p className="eyebrow flex items-center gap-2">
            Audit report
            {refreshing ? <span className="font-sans text-[11px] font-normal normal-case tracking-normal text-ink-3">· refreshing…</span> : null}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2.5">
            <SheetTitle className="font-mono text-[19px] font-medium leading-tight tracking-[-0.02em] text-ink">{invoice.invoice_number ?? "Invoice"}</SheetTitle>
            <StatusBadge status={invoice.status} />
          </div>
          <SheetDescription className="mt-1 flex min-w-0 flex-wrap items-center gap-x-1.5 text-[13px] text-ink-3">
            <span className="truncate text-ink-2">{invoice.vendor.name ?? "Unknown vendor"}</span>
            <span aria-hidden>·</span>
            <Money amount={invoice.financial_summary.total} currency={invoice.financial_summary.currency} className="text-ink-2" />
          </SheetDescription>
        </div>
        <div className="flex items-center gap-1.5">
          {sequence && position >= 0 ? (
            <div className="mr-1 flex items-center gap-1">
              <span className="figure mr-1 text-[12px] text-ink-3">
                {position + 1} / {sequence.length}
              </span>
              <Tooltip content={<span>Previous <Kbd>K</Kbd></span>}>
                <Button size="icon-sm" variant="outline" aria-label="Previous report" disabled={!previous} onClick={() => previous && onNavigate(previous)}>
                  <ChevronUp />
                </Button>
              </Tooltip>
              <Tooltip content={<span>Next <Kbd>J</Kbd></span>}>
                <Button size="icon-sm" variant="outline" aria-label="Next report" disabled={!next} onClick={() => next && onNavigate(next)}>
                  <ChevronDown />
                </Button>
              </Tooltip>
            </div>
          ) : null}
          <ExportMenu onExport={(format) => onExport(invoice, format)} />
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-7 overflow-y-auto px-5 py-5">
        <ApprovalCard invoice={invoice} now={now} saving={saving} />

        <Section title="Verification checks" aside={`${passed} of ${ran} passed without a finding`}>
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line">
            {checks.map((check) => (
              <CheckRow key={check.id} check={check} />
            ))}
          </ul>
        </Section>

        <Section title="Tax verification">
          <TaxVerification invoice={invoice} checks={checks} />
        </Section>

        <Section title="Line items & figures">
          <ExtractedFields invoice={invoice} spikedLines={spikedLines} />
        </Section>

        <Section title="Security & fraud flags" aside={invoice.anomalies.length ? plural(invoice.anomalies.length, "finding") : undefined}>
          <SecurityFlags anomalies={invoice.anomalies} userName={userName} />
        </Section>

        <Section title="Audit trail">
          <Timeline events={trail} />
        </Section>

        <Section title="Source document">
          <DocumentEvidence invoice={invoice} />
        </Section>
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-line bg-sunken/60 px-5 py-3">
        <p className="mr-auto text-[11px] text-ink-3">
          Invoice ID <span className="figure text-ink-2">{invoice.invoice_id}</span>
        </p>
        <Button size="sm" variant="secondary" onClick={() => onExport(invoice, "csv")}>
          <FileSpreadsheet aria-hidden /> Line items CSV
        </Button>
        <Button size="sm" variant="primary" onClick={() => onExport(invoice, "json")}>
          <Braces aria-hidden /> Export audit JSON
        </Button>
      </footer>
    </>
  );
}

function Section({ title, aside, children }: { title: string; aside?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="eyebrow">{title}</h3>
        {aside ? <span className="text-[11px] text-ink-3">{aside}</span> : null}
      </div>
      {children}
    </section>
  );
}

function ExportMenu({ onExport }: { onExport: (format: ExportFormat) => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="secondary">
          <Download aria-hidden /> Export
          <ChevronDown className="-mr-0.5 text-ink-3" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuItem onSelect={() => onExport("json")}>
          <Braces className="text-ink-3" aria-hidden />
          <span className="flex-1">
            Full audit record
            <span className="block text-[11px] text-ink-3">Findings, resolutions, approval</span>
          </span>
          <span className="font-mono text-[11px] text-ink-3">JSON</span>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onExport("csv")}>
          <FileSpreadsheet className="text-ink-3" aria-hidden />
          <span className="flex-1">
            Line items
            <span className="block text-[11px] text-ink-3">For spreadsheets and ERPs</span>
          </span>
          <span className="font-mono text-[11px] text-ink-3">CSV</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ApprovalCard({ invoice, now, saving }: { invoice: InvoiceRecord; now: number; saving: boolean }) {
  const approval = invoice.approval;
  if (!approval) {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-line bg-sunken/60 px-4 py-3.5">
        <CircleDashed className="mt-0.5 size-5 shrink-0 text-ink-3" aria-hidden />
        <div>
          <p className="text-[13px] font-medium text-ink">Not approved</p>
          <p className="mt-0.5 text-[12px] text-ink-2">This invoice is {invoice.status.toLowerCase().replace("_", " ")}; it has no approval on record.</p>
        </div>
      </div>
    );
  }
  const note = invoice.anomalies
    .filter((a) => a.status === "DISMISSED" && a.resolved_by === approval.by?.id && a.resolution_note)
    .sort((a, b) => (b.resolved_at ?? "").localeCompare(a.resolved_at ?? ""))[0]?.resolution_note;

  return (
    <div className="relative overflow-hidden rounded-xl border border-approved/25 bg-approved/[0.06] px-4 py-4">
      <div className="pointer-events-none absolute -right-10 -top-12 size-40 rounded-full bg-approved/10 blur-2xl" aria-hidden />
      <div className="relative flex items-start gap-3.5">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-approved text-on-approved shadow-[var(--shadow-card)]" aria-hidden>
          <ShieldCheck className="size-5" strokeWidth={1.75} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-semibold text-ink">
            {approvalSentence(approval, now)}
            {saving ? <span className="ml-2 text-[12px] font-normal text-ink-3">saving…</span> : null}
          </p>
          <p className="mt-0.5 text-[12px] text-ink-2">
            <span className="figure">{formatDateTime(approval.at)}</span> ·{" "}
            {approval.method === "automatic" ? "Rule engine: no blocking finding, confidence at or above threshold" : "Human-in-the-loop approval"}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {approval.method === "automatic" ? (
              <span className="inline-flex h-6 items-center gap-1.5 rounded-full bg-surface-solid px-2 text-[12px] text-ink-2 ring-1 ring-inset ring-line">
                <Bot className="size-3.5 text-ink-3" aria-hidden /> Rule engine
              </span>
            ) : (
              <span className="inline-flex h-6 items-center gap-1.5 rounded-full bg-surface-solid pl-0.5 pr-2 text-[12px] text-ink ring-1 ring-inset ring-line">
                <Avatar name={approval.by?.name ?? "?"} className="size-5 text-[9px]" /> {approval.by?.name ?? "Reviewer"}
              </span>
            )}
            {note ? <span className="min-w-0 max-w-full truncate text-[12px] italic text-ink-2">“{note}”</span> : null}
          </div>
        </div>
      </div>
    </div>
  );
}

const CHECK_STATE: Record<CheckState, { icon: LucideIcon; tone: string; label: string; pill: string }> = {
  passed: { icon: CircleCheck, tone: "text-approved", label: "Passed", pill: "bg-approved/10 text-approved ring-approved/20" },
  overridden: { icon: ShieldAlert, tone: "text-review", label: "Overridden", pill: "bg-review/10 text-review ring-review/25" },
  open: { icon: TriangleAlert, tone: "text-sev-medium", label: "Noted", pill: "bg-review/10 text-review ring-review/25" },
  confirmed: { icon: ShieldX, tone: "text-rejected", label: "Confirmed", pill: "bg-rejected/10 text-rejected ring-rejected/20" },
  "not-run": { icon: CircleSlash, tone: "text-ink-3", label: "Not run", pill: "bg-ink-3/10 text-ink-3 ring-line" },
};

function CheckRow({ check }: { check: VerificationCheck }) {
  const { icon: Icon, tone, label, pill } = CHECK_STATE[check.state];
  return (
    <li className="flex items-center gap-3 bg-surface-solid px-3.5 py-2.5">
      <Icon className={cn("size-4 shrink-0", tone)} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-ink">{check.label}</p>
        <p className="truncate text-[12px] text-ink-3" title={check.detail}>
          {check.detail}
        </p>
      </div>
      <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", pill)}>{label}</span>
    </li>
  );
}

function TaxVerification({ invoice, checks }: { invoice: InvoiceRecord; checks: VerificationCheck[] }) {
  const { subtotal, tax, currency } = invoice.financial_summary;
  const rate = effectiveTaxRate(invoice);
  const taxCheck = checks.find((check) => check.id === "tax")!;
  const finding = taxCheck.findings[0];
  const limit = finding?.expected_value?.max_rate_percent;
  const source = finding?.expected_value?.limit_source;
  const rows: { label: string; value: ReactNode }[] = [
    { label: "Taxable subtotal", value: subtotal ? `${formatAmount(subtotal, currency)} ${currency}` : "—" },
    { label: "Tax charged", value: `${formatAmount(tax, currency)} ${currency}` },
    { label: "Effective rate", value: rate === null ? "—" : `${rate.toFixed(2)}%` },
    { label: "Country limit", value: limit !== undefined ? `${String(limit)}%${source ? ` (${String(source)})` : ""}` : "No finding raised" },
    { label: "Vendor tax ID", value: invoice.vendor.tax_id ?? "Not printed" },
  ];
  const { icon: Icon, tone, label } = CHECK_STATE[taxCheck.state];
  return (
    <div className="overflow-hidden rounded-xl border border-line">
      {/* Solid cells over 1px gaps draw the hairlines at either column count. */}
      <dl className="grid grid-cols-2 gap-px bg-line sm:grid-cols-3">
        {rows.map((row) => (
          <div key={row.label} className="min-w-0 bg-surface-solid px-3.5 py-2.5">
            <dt className="text-[11px] text-ink-3">{row.label}</dt>
            <dd className="figure mt-0.5 truncate text-[13px] text-ink">{row.value}</dd>
          </div>
        ))}
        <div className={cn("flex items-center gap-2 bg-surface-solid px-3.5 py-2.5", tone)}>
          <Icon className="size-4 shrink-0" aria-hidden />
          <span className="text-[12px] font-medium">{label}</span>
        </div>
      </dl>
    </div>
  );
}

function SecurityFlags({ anomalies, userName }: { anomalies: Anomaly[]; userName: (id: string | null) => string }) {
  if (anomalies.length === 0) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-line bg-surface-solid px-4 py-3.5">
        <ShieldCheck className="size-5 shrink-0 text-approved" aria-hidden />
        <p className="text-[13px] text-ink-2">No security or data-quality flag was raised. Every fraud, duplicate and integrity rule passed.</p>
      </div>
    );
  }
  const groups = [
    { title: "Fraud signals", items: anomalies.filter((a) => ANOMALY_INFO[a.type].kind === "fraud").sort(bySeverity) },
    { title: "Data quality", items: anomalies.filter((a) => ANOMALY_INFO[a.type].kind === "quality").sort(bySeverity) },
  ].filter((group) => group.items.length > 0);

  return (
    <div className="flex flex-col gap-4">
      {groups.map((group) => (
        <div key={group.title}>
          <p className="mb-2 text-[12px] font-medium text-ink-2">
            {group.title} <span className="figure text-ink-3">{group.items.length}</span>
          </p>
          <ul className="flex flex-col gap-2">
            {group.items.map((anomaly) => (
              <FlagItem key={anomaly.id} anomaly={anomaly} userName={userName} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function FlagItem({ anomaly, userName }: { anomaly: Anomaly; userName: (id: string | null) => string }) {
  const info = ANOMALY_INFO[anomaly.type];
  const dismissed = anomaly.status === "DISMISSED";
  return (
    <li className="rounded-xl border border-line bg-surface-solid px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <SeverityTag severity={anomaly.severity} />
        <span className="text-[13px] font-medium text-ink">{info.label}</span>
        <span className="figure ml-auto text-[11px] text-ink-3">{Math.round(anomaly.confidence)}% rule confidence</span>
      </div>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-2">{anomaly.description}</p>
      <p className="mt-2 flex flex-wrap items-center gap-x-1.5 border-t border-line pt-2 text-[12px] text-ink-3">
        {anomaly.status === "OPEN" ? (
          <>
            <CircleDashed className="size-3.5" aria-hidden /> Left open (informational)
          </>
        ) : (
          <>
            {dismissed ? <CircleCheck className="size-3.5 text-approved" aria-hidden /> : <ShieldX className="size-3.5 text-rejected" aria-hidden />}
            <span className="text-ink-2">
              {dismissed ? "Dismissed" : "Confirmed"} by {userName(anomaly.resolved_by)}
            </span>
            {anomaly.resolved_at ? <span>· {formatDateTime(anomaly.resolved_at)}</span> : null}
            {anomaly.resolution_note ? <span className="min-w-0 basis-full truncate italic text-ink-2">“{anomaly.resolution_note}”</span> : null}
          </>
        )}
      </p>
    </li>
  );
}

const TRAIL_ICON: Record<TrailEvent["kind"], { icon: LucideIcon; tone: string }> = {
  received: { icon: FileInput, tone: "text-ink-2 bg-surface-solid" },
  extracted: { icon: ScanText, tone: "text-accent bg-accent/10" },
  flagged: { icon: TriangleAlert, tone: "text-review bg-review/10" },
  dismissed: { icon: Check, tone: "text-ink-2 bg-surface-solid" },
  confirmed: { icon: ShieldX, tone: "text-rejected bg-rejected/10" },
  approved: { icon: ShieldCheck, tone: "text-on-approved bg-approved" },
};

function Timeline({ events }: { events: TrailEvent[] }) {
  return (
    <ol className="relative flex flex-col gap-4 pl-1">
      <span className="absolute bottom-3 left-[15px] top-3 w-px bg-line" aria-hidden />
      {events.map((event) => {
        const { icon: Icon, tone } = TRAIL_ICON[event.kind];
        return (
          <li key={event.id} className="relative flex items-start gap-3">
            <span className={cn("relative flex size-[22px] shrink-0 items-center justify-center rounded-full ring-1 ring-line ring-offset-2 ring-offset-[var(--surface-solid)]", tone)} aria-hidden>
              <Icon className="size-3" />
            </span>
            <div className="min-w-0 flex-1 pt-px">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <p className="text-[13px] text-ink">{event.title}</p>
                <time className="figure text-[11px] text-ink-3" dateTime={event.at}>
                  {formatDateTime(event.at)}
                </time>
              </div>
              {event.detail ? <p className="mt-0.5 truncate text-[12px] text-ink-3">{event.detail}</p> : null}
              {event.note ? <p className="mt-1 text-[12px] italic text-ink-2">“{event.note}”</p> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function DocumentEvidence({ invoice }: { invoice: InvoiceRecord }) {
  const [copied, setCopied] = useState(false);
  const { document } = invoice;
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface-solid">
      <div className="flex flex-wrap items-center gap-3 px-3.5 py-3">
        <FileInput className="size-4 shrink-0 text-ink-3" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] text-ink" title={document.filename}>
            {document.filename}
          </p>
          <p className="text-[12px] text-ink-3">
            {document.content_type} · {formatBytes(document.size_bytes)} · {invoice.source === "upload" ? "uploaded" : "via API"}
          </p>
        </div>
        {document.available ? (
          <Button size="sm" variant="secondary" asChild>
            <a href={api.documentUrl(invoice.invoice_id)} target="_blank" rel="noopener noreferrer">
              <ExternalLink aria-hidden /> Open original
            </a>
          </Button>
        ) : (
          <span className="text-[12px] text-ink-3">Original not stored</span>
        )}
      </div>
      <div className="flex items-center gap-2 border-t border-line bg-sunken/50 px-3.5 py-2.5">
        <span className="eyebrow shrink-0">SHA-256</span>
        <span className="figure min-w-0 flex-1 truncate text-[11.5px] text-ink-2" title={document.sha256}>
          {document.sha256 || "—"}
        </span>
        {document.sha256 ? (
          <Tooltip content={copied ? "Copied" : "Copy fingerprint"}>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Copy SHA-256 fingerprint"
              onClick={() => navigator.clipboard?.writeText(document.sha256).then(() => setCopied(true), () => undefined)}
            >
              {copied ? <Check className="text-approved" /> : <Copy />}
            </Button>
          </Tooltip>
        ) : null}
      </div>
    </div>
  );
}
