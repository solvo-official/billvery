import { ArrowUpRight, Check, CircleCheck, ShieldAlert, ShieldX, X } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { SEVERITY, SeverityTag } from "@/components/audit/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime } from "@/lib/dates";
import { formatAmount, isZero, subtractAmounts, toUnits } from "@/lib/money";
import { ANOMALY_INFO, isBlocking } from "@/lib/rules";
import type { Anomaly, InvoiceRecord, Resolution } from "@/lib/types";
import { IdentifierDiff } from "./ExtractedFields";

interface PriorSubmission {
  invoice_id: string;
  invoice_number: string | null;
  submitted_at?: string;
  vendor_name?: string | null;
}

interface AnomalyCardProps {
  anomaly: Anomaly;
  invoice: InvoiceRecord;
  staged: Resolution | undefined;
  pointed: boolean;
  disabled: boolean;
  onStage: (resolution: Resolution | undefined) => void;
  onPoint: (active: boolean) => void;
  userName: (userId: string | null) => string;
  onOpenInvoice: (invoiceId: string) => void;
  canOpen: (invoiceId: string) => boolean;
}

export function AnomalyCard(props: AnomalyCardProps) {
  const { anomaly, staged, pointed, disabled, onStage, onPoint } = props;
  const info = ANOMALY_INFO[anomaly.type];
  const open = anomaly.status === "OPEN";
  const severity = SEVERITY[anomaly.severity];

  return (
    <article
      aria-label={`${anomaly.severity} finding: ${info.label}`}
      onMouseEnter={() => onPoint(true)}
      onMouseLeave={() => onPoint(false)}
      onFocusCapture={() => onPoint(true)}
      onBlurCapture={() => onPoint(false)}
      className={cn(
        "rounded-[6px] border bg-raised/35 transition-[border-color,background-color]",
        open && isBlocking(anomaly.severity) ? severity.border : "border-line",
        pointed && "bg-raised/80",
        staged === "DISMISSED" && "border-approved/45",
        staged === "CONFIRMED" && "border-rejected/60",
        !open && "opacity-90",
      )}
    >
      <header className="flex items-start justify-between gap-3 px-3.5 pt-3">
        <div className="min-w-0">
          <SeverityTag severity={anomaly.severity} />
          <h4 className="mt-1 text-[14px] font-medium leading-snug text-ink">{info.label}</h4>
          <Tooltip content={info.rule} side="bottom" align="start">
            <span className="figure mt-0.5 inline-block cursor-help text-[11px] text-ink-3" tabIndex={0}>
              {anomaly.type}
            </span>
          </Tooltip>
        </div>
        <div className="shrink-0 text-right">
          <span className="figure text-[13px] text-ink">{Math.round(anomaly.confidence)}%</span>
          <span className="block text-[11px] text-ink-3">rule confidence</span>
        </div>
      </header>

      <p className="px-3.5 pt-2 text-[12.5px] leading-relaxed text-ink-2">{anomaly.description}</p>

      <div className="px-3.5 pb-3 pt-3">
        <Comparison {...props} />
      </div>

      {open ? (
        <footer className="flex flex-wrap items-center gap-2 border-t border-line px-3.5 py-2.5">
          <div role="group" aria-label={`Decision for ${info.label}`} className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant={staged === "DISMISSED" ? "approve-outline" : "secondary"}
              aria-pressed={staged === "DISMISSED"}
              disabled={disabled}
              onClick={() => onStage(staged === "DISMISSED" ? undefined : "DISMISSED")}
            >
              {staged === "DISMISSED" ? <Check aria-hidden /> : <X aria-hidden />}
              Dismiss anomaly
            </Button>
            <Button
              size="sm"
              variant={staged === "CONFIRMED" ? "danger" : "danger-outline"}
              aria-pressed={staged === "CONFIRMED"}
              disabled={disabled}
              onClick={() => onStage(staged === "CONFIRMED" ? undefined : "CONFIRMED")}
            >
              <ShieldAlert aria-hidden />
              {info.kind === "fraud" ? "Confirm fraud" : "Confirm issue"}
            </Button>
          </div>
          <span className="ml-auto text-[11px] text-ink-3">
            {staged === "DISMISSED"
              ? "Dismissal staged"
              : staged === "CONFIRMED"
                ? isBlocking(anomaly.severity)
                  ? "Confirmation staged · rejects the invoice"
                  : "Confirmation staged"
                : "Undecided"}
          </span>
        </footer>
      ) : (
        <ResolvedFooter anomaly={anomaly} userName={props.userName} />
      )}
    </article>
  );
}

function ResolvedFooter({ anomaly, userName }: { anomaly: Anomaly; userName: (id: string | null) => string }) {
  const dismissed = anomaly.status === "DISMISSED";
  return (
    <footer className="flex items-start gap-2 border-t border-line px-3.5 py-2.5 text-[12px]">
      {dismissed ? <CircleCheck className="mt-0.5 size-3.5 shrink-0 text-approved" aria-hidden /> : <ShieldX className="mt-0.5 size-3.5 shrink-0 text-rejected" aria-hidden />}
      <div className="min-w-0">
        <p className="text-ink-2">
          <span className="font-medium text-ink">{dismissed ? "Dismissed" : "Confirmed"}</span> by {userName(anomaly.resolved_by)}
          {anomaly.resolved_at ? <span className="text-ink-3"> · {formatDateTime(anomaly.resolved_at)}</span> : null}
        </p>
        {anomaly.resolution_note ? <p className="mt-0.5 text-ink-2">“{anomaly.resolution_note}”</p> : null}
      </div>
    </footer>
  );
}

// --- Detected vs expected -------------------------------------------------------------------

interface CompareRow {
  label: string;
  detected: ReactNode;
  expected: ReactNode;
}

function CompareGrid({ rows, severityColor }: { rows: CompareRow[]; severityColor: string }) {
  return (
    <div className="grid grid-cols-[minmax(80px,auto)_minmax(0,1fr)_minmax(0,1fr)] overflow-hidden rounded-[5px] border border-line text-[12px]">
      <div className="bg-raised/70 px-2.5 py-1.5" />
      <div className="eyebrow flex items-center gap-1.5 bg-raised/70 px-2.5 py-1.5">
        <span className={cn("size-1.5 rounded-full", severityColor)} aria-hidden />
        Detected
      </div>
      <div className="eyebrow bg-raised/70 px-2.5 py-1.5">Expected</div>
      {rows.map((row) => (
        <Fragment key={row.label}>
          <div className="border-t border-line px-2.5 py-1.5 text-ink-3">{row.label}</div>
          <div className="min-w-0 break-words border-t border-line px-2.5 py-1.5 font-medium text-ink">{row.detected}</div>
          <div className="min-w-0 break-words border-t border-line px-2.5 py-1.5 text-ink-2">{row.expected}</div>
        </Fragment>
      ))}
    </div>
  );
}

function PriorList({
  heading,
  items,
  onOpenInvoice,
  canOpen,
}: {
  heading: string;
  items: PriorSubmission[];
  onOpenInvoice: (id: string) => void;
  canOpen: (id: string) => boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2.5">
      <p className="text-[11px] text-ink-3">{heading}</p>
      <ul className="mt-1 flex flex-col gap-1">
        {items.map((item) => (
          <li key={item.invoice_id} className="flex items-center gap-2 text-[12px]">
            <span className="figure text-ink">{item.invoice_number ?? item.invoice_id.slice(0, 8)}</span>
            {item.submitted_at ? <span className="text-ink-3">received {formatDate(item.submitted_at.slice(0, 10))}</span> : null}
            {canOpen(item.invoice_id) ? (
              <Button size="xs" variant="ghost" className="ml-auto" onClick={() => onOpenInvoice(item.invoice_id)}>
                Open original <ArrowUpRight aria-hidden />
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

const asString = (value: unknown) => (typeof value === "string" ? value : value === null || value === undefined ? "—" : String(value));
const shortHash = (hash: unknown) => {
  const value = asString(hash);
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
};

function Comparison({ anomaly, invoice, onOpenInvoice, canOpen }: AnomalyCardProps) {
  const detected = anomaly.detected_value ?? {};
  const expected = anomaly.expected_value ?? {};
  const currency = invoice.financial_summary.currency;
  const amount = (value: unknown) => (typeof value === "string" ? `${formatAmount(value, currency)}` : "—");
  const dot = SEVERITY[anomaly.severity].bg;
  const grid = (rows: CompareRow[]) => <CompareGrid rows={rows} severityColor={dot} />;

  switch (anomaly.type) {
    case "MATH_TOTAL_MISMATCH":
      return <FootingWorksheet anomaly={anomaly} invoice={invoice} />;

    case "DUPLICATE_FILE_HASH":
      return (
        <>
          {grid([{ label: "File SHA-256", detected: <span className="figure">{shortHash(detected.file_hash)}</span>, expected: "Not seen before in this organization" }])}
          <PriorList
            heading="Byte-identical file already received as"
            items={(detected.duplicate_of as PriorSubmission[] | undefined) ?? []}
            onOpenInvoice={onOpenInvoice}
            canOpen={canOpen}
          />
        </>
      );

    case "DUPLICATE_INVOICE_NUMBER":
      return (
        <>
          {grid([
            {
              label: "Invoice no.",
              detected: <span className="figure">{asString(detected.invoice_number)}</span>,
              expected: `Unique per vendor within ${asString(expected.window_days)} days`,
            },
          ])}
          <PriorList heading="Same number already submitted as" items={(detected.matches as PriorSubmission[] | undefined) ?? []} onOpenInvoice={onOpenInvoice} canOpen={canOpen} />
        </>
      );

    case "SIMILAR_INVOICE_NUMBER": {
      const match = (detected.matches as PriorSubmission[] | undefined)?.[0];
      const value = asString(detected.invoice_number);
      return grid([
        {
          label: "Invoice no.",
          detected: match?.invoice_number ? <IdentifierDiff value={value} reference={match.invoice_number} /> : value,
          expected: match?.invoice_number ? <span className="figure">{match.invoice_number} (earlier)</span> : "A distinct number",
        },
      ]);
    }

    case "VENDOR_TAX_ID_MISMATCH": {
      // Both sides as the rule compared them: normalized upper-case alphanumerics.
      const printed = asString(detected.vendor_tax_id);
      const onFile = asString(expected.vendor_tax_id);
      return (
        <>
          {grid([
            {
              label: "Tax ID",
              detected: <IdentifierDiff value={printed} reference={onFile} />,
              expected: <IdentifierDiff value={onFile} reference={printed} tone="neutral" />,
            },
          ])}
          <p className="mt-2 text-[11px] text-ink-3">
            Printed as <span className="figure text-ink-2">{invoice.vendor.tax_id ?? printed}</span>. Verify by phone using the contact
            in your vendor master, not one printed on this invoice.
          </p>
        </>
      );
    }

    case "VENDOR_NAME_MISMATCH":
      return grid([
        { label: "Vendor", detected: asString(detected.vendor_name), expected: `${asString(expected.vendor_name)} (owner of this tax ID)` },
        {
          label: "Similarity",
          detected: <span className="figure">{Math.round(Number(expected.name_similarity ?? 0) * 100)}%</span>,
          expected: "Same legal name",
        },
      ]);

    case "SIMILAR_VENDOR_NAME": {
      const similar = (expected.similar_vendors as { name: string; similarity: number }[] | undefined) ?? [];
      return grid(
        similar.slice(0, 2).map((vendor, index) => ({
          label: index === 0 ? "Vendor" : "Also like",
          detected: index === 0 ? asString(detected.vendor_name) : "",
          expected: (
            <span>
              {vendor.name} <span className="figure text-ink-3">· {Math.round(vendor.similarity * 100)}% similar</span>
            </span>
          ),
        })),
      );
    }

    case "PRICE_SPIKE":
      return (
        <>
          {grid([
            { label: `Unit price, line ${asString(detected.line_number)}`, detected: <span className="figure">{amount(detected.unit_price)}</span>, expected: <span className="figure">{amount(expected.historical_average_unit_price)} avg</span> },
            { label: "Ratio", detected: <span className="figure">{asString(detected.ratio)}×</span>, expected: <span className="figure">&lt; {asString(expected.spike_ratio_threshold)}×</span> },
          ])}
          <p className="mt-2 text-[11px] text-ink-3">
            Average of {asString(expected.samples)} earlier purchases of “{asString(detected.description)}” across all vendors, {currency}, last 365 days.
          </p>
        </>
      );

    case "TAX_RATE_EXCEEDED":
      return (
        <>
          {grid([
            { label: "Tax rate", detected: <span className="figure">{Number(detected.effective_rate_percent).toFixed(2)}%</span>, expected: <span className="figure">≤ {asString(expected.max_rate_percent)}%</span> },
            { label: "Tax amount", detected: <span className="figure">{amount(detected.tax_amount)}</span>, expected: <span className="figure">≤ {amount(expected.max_tax_amount)}</span> },
          ])}
          <p className="mt-2 text-[11px] text-ink-3">Limit from {asString(expected.limit_source)}.</p>
        </>
      );

    case "LOW_EXTRACTION_CONFIDENCE": {
      const rows: CompareRow[] = [
        { label: "Overall", detected: <span className="figure">{asString(detected.confidence)}%</span>, expected: <span className="figure">≥ {asString(expected.minimum_confidence)}%</span> },
      ];
      if (invoice.vendor.confidence !== null) {
        rows.push({ label: "Vendor name", detected: <span className="figure">{invoice.vendor.confidence}%</span>, expected: <span className="figure">≥ 90%</span> });
      }
      return (
        <>
          {grid(rows)}
          <p className="mt-2 text-[11px] text-ink-3">Check the totals, dates and vendor against the original file before dismissing.</p>
        </>
      );
    }

    case "FUTURE_INVOICE_DATE":
      return grid([
        { label: "Issue date", detected: formatDate(asString(detected.invoice_date)), expected: `On or before ${formatDate(asString(expected.latest_allowed_date))}` },
      ]);

    case "STALE_INVOICE_DATE":
      return grid([
        { label: "Issue date", detected: formatDate(asString(detected.invoice_date)), expected: `On or after ${formatDate(asString(expected.earliest_expected_date))}` },
      ]);

    case "MISSING_REQUIRED_FIELDS":
      return grid(
        ((detected.missing_fields as string[] | undefined) ?? []).map((field) => ({
          label: field.replace(/_/g, " "),
          detected: <span className="text-ink-3">Not found</span>,
          expected: "Present on every invoice",
        })),
      );
  }
}

/** The audit "footing": re-adding the invoice's own figures and comparing to what it printed. */
function FootingWorksheet({ anomaly, invoice }: { anomaly: Anomaly; invoice: InvoiceRecord }) {
  const money = invoice.financial_summary;
  const currency = money.currency;
  const fmt = (value: string) => formatAmount(value, currency);
  const expected = asString(anomaly.expected_value?.total_amount);
  const printed = money.total ?? "0";
  const variance = subtractAmounts(printed, expected);
  const positive = toUnits(variance) > 0n;
  const percent = anomaly.detected_value?.difference_percent;

  const line = (label: string, value: string, sign = "") => (
    <>
      <dt className="font-sans text-ink-3">
        <span className="inline-block w-3 text-ink-3">{sign}</span>
        {label}
      </dt>
      <dd className="text-right text-ink-2">{fmt(value)}</dd>
    </>
  );

  return (
    <div className="overflow-hidden rounded-[5px] border border-line text-[12px]">
      <div className="flex items-center justify-between bg-raised/70 px-2.5 py-1.5">
        <span className="eyebrow">Footing worksheet</span>
        <span className="figure text-[11px] text-ink-3">{currency}</span>
      </div>
      <dl className="figure grid grid-cols-[1fr_auto] gap-y-1 border-t border-line px-2.5 py-2">
        {money.subtotal ? line("Subtotal", money.subtotal) : null}
        {line("Tax", money.tax, "+")}
        {!isZero(money.shipping) ? line("Shipping", money.shipping, "+") : null}
        {!isZero(money.discount) ? line("Discount", money.discount, "−") : null}
        <dt className="mt-1 border-t border-line-strong pt-1 font-sans text-ink-2">
          <span className="inline-block w-3">=</span>Expected total
        </dt>
        <dd className="mt-1 border-t border-line-strong pt-1 text-right">
          <span className="border-b-[3px] border-double border-line-strong pb-px text-ink">{fmt(expected)}</span>
        </dd>
        <dt className="mt-1.5 font-sans text-ink-2">
          <span className="inline-block w-3" />
          Printed total <span className="ml-1 rounded-[3px] bg-raised px-1 text-[10px] text-ink-3">detected</span>
        </dt>
        <dd className="mt-1.5 text-right font-medium text-ink">{fmt(printed)}</dd>
        <dt className="font-sans text-ink-2">
          <span className="inline-block w-3" />
          Variance
        </dt>
        <dd className={cn("text-right", SEVERITY[anomaly.severity].text)}>
          {positive ? "+" : "−"}
          {fmt(variance.replace("-", ""))}
          {typeof percent === "number" ? <span className="ml-1.5 text-ink-3">{percent.toFixed(2)}%</span> : null}
        </dd>
      </dl>
    </div>
  );
}
