import { Eye, EyeOff, FileImage, FileText, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { SEVERITY } from "@/components/audit/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/dates";
import { formatAmount, isZero } from "@/lib/money";
import { SEVERITY_RANK } from "@/lib/rules";
import type { DocField, InvoiceRecord, Organization, Severity } from "@/lib/types";

/** The API field each region of the page was extracted into. */
const FIELD_KEY: Record<string, string> = {
  vendor: "vendor_name",
  tax_id: "vendor_tax_id",
  invoice_number: "invoice_number",
  invoice_date: "invoice_date",
  due_date: "due_date",
  subtotal: "subtotal",
  tax: "tax_amount",
  shipping: "shipping_amount",
  discount: "discount_amount",
  total: "total_amount",
};
const fieldKey = (field: DocField) => (field.startsWith("line:") ? `line_items[${Number(field.slice(5)) - 1}].unit_price` : FIELD_KEY[field] ?? field);

const ZOOM_STEPS = [0.75, 0.875, 1, 1.125, 1.25, 1.5];
const MODE_STORAGE_KEY = "invoice-auditor.viewer-mode";
type Mode = "extracted" | "original";

function savedMode(): Mode {
  try {
    return window.localStorage.getItem(MODE_STORAGE_KEY) === "original" ? "original" : "extracted";
  } catch {
    return "extracted";
  }
}

interface DocumentViewerProps {
  invoice: InvoiceRecord;
  organization: Organization;
  /** Regions tied to open or confirmed findings, with the strongest severity per region. */
  flagged: Map<DocField, Severity>;
  /** Regions of the finding the reviewer is pointing at right now. */
  emphasized: DocField[];
}

export function DocumentViewer({ invoice, organization, flagged, emphasized }: DocumentViewerProps) {
  const [zoomIndex, setZoomIndex] = useState(2);
  const [showBoxes, setShowBoxes] = useState(true);
  const [preferred, setPreferred] = useState<Mode>(savedMode);
  const scrollRef = useRef<HTMLDivElement>(null);
  const zoom = ZOOM_STEPS[zoomIndex]!;
  const emphasisKey = emphasized.join("|");
  const hasOriginal = invoice.document.available;
  const mode: Mode = preferred === "original" && hasOriginal ? "original" : "extracted";
  const isPdf = invoice.document.content_type === "application/pdf";

  const choose = (next: Mode) => {
    setPreferred(next);
    try {
      window.localStorage.setItem(MODE_STORAGE_KEY, next);
    } catch {
      // storage unavailable: the choice lasts for this visit
    }
  };

  useEffect(() => {
    const first = emphasized[0];
    if (!first || mode !== "extracted") return;
    const target = scrollRef.current?.querySelector(`[data-field="${CSS.escape(first)}"]`);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    target?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: reduced ? "auto" : "smooth" });
  }, [emphasisKey, mode]); // keyed on the joined list: a new array with the same fields must not re-scroll

  const box = (field: DocField): BoxState => {
    const severity = flagged.get(field);
    return { visible: showBoxes || severity !== undefined, severity, emphasized: emphasized.includes(field) };
  };

  const DocIcon = isPdf ? FileText : FileImage;
  const worst = [...flagged.values()].sort((a, b) => SEVERITY_RANK[b] - SEVERITY_RANK[a])[0];
  const zoomable = mode === "extracted" || !isPdf;

  return (
    <div className="flex min-h-0 flex-col bg-sunken">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <div role="group" aria-label="Document view" className="flex rounded-[5px] border border-line-strong p-0.5">
          <ModeButton active={mode === "extracted"} onClick={() => choose("extracted")}>
            Extracted fields
          </ModeButton>
          {hasOriginal ? (
            <ModeButton active={mode === "original"} onClick={() => choose("original")}>
              Original file
            </ModeButton>
          ) : (
            <Tooltip content="This invoice arrived through the API; its original file isn't stored by the audit service.">
              <span>
                <ModeButton active={false} disabled onClick={() => undefined}>
                  Original file
                </ModeButton>
              </span>
            </Tooltip>
          )}
        </div>
        <DocIcon className="ml-1 size-4 shrink-0 text-ink-3" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2" title={invoice.document.filename}>
          {invoice.document.filename}
        </span>
        {zoomable ? (
          <>
            <Button size="icon-sm" variant="ghost" aria-label="Zoom out" disabled={zoomIndex === 0} onClick={() => setZoomIndex(zoomIndex - 1)}>
              <Minus />
            </Button>
            <span className="figure w-10 text-center text-[11px] text-ink-2" aria-live="polite">
              {Math.round(zoom * 100)}%
            </span>
            <Button size="icon-sm" variant="ghost" aria-label="Zoom in" disabled={zoomIndex === ZOOM_STEPS.length - 1} onClick={() => setZoomIndex(zoomIndex + 1)}>
              <Plus />
            </Button>
          </>
        ) : null}
        {mode === "extracted" ? (
          <Button size="sm" variant={showBoxes ? "secondary" : "ghost"} aria-pressed={showBoxes} onClick={() => setShowBoxes(!showBoxes)}>
            {showBoxes ? <Eye /> : <EyeOff />}
            Field boxes
          </Button>
        ) : null}
      </div>

      {mode === "original" && isPdf ? (
        <iframe
          key={invoice.invoice_id}
          src={`${api.documentUrl(invoice.invoice_id)}#view=FitH`}
          title={`Original document: ${invoice.document.filename}`}
          className="min-h-0 w-full flex-1 bg-white"
        />
      ) : (
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-4 py-6 sm:px-8">
          {mode === "original" ? (
            <div className="mx-auto w-fit max-w-none" style={{ zoom } as CSSProperties}>
              <img
                src={api.documentUrl(invoice.invoice_id)}
                alt={`Original document: ${invoice.document.filename}`}
                className="block max-w-[720px] bg-white shadow-[0_18px_48px_rgba(0,0,0,0.5)]"
              />
            </div>
          ) : (
            <div className="mx-auto w-[612px] max-w-none" style={{ zoom } as CSSProperties}>
              <Paper invoice={invoice} organization={organization} box={box} />
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-3 py-2 text-[11px] text-ink-3">
        {mode === "extracted" ? (
          <>
            <span>Rebuilt from the extracted fields: compare it with the original.</span>
            {worst ? (
              <span className="flex items-center gap-1.5">
                <span className="h-2.5 w-4 rounded-[2px] border-2" style={{ borderColor: SEVERITY[worst].paper }} aria-hidden />
                Flagged by a rule
              </span>
            ) : null}
          </>
        ) : (
          <span>
            Original upload · <span className="figure">sha256 {invoice.document.sha256.slice(0, 12)}…</span>
          </span>
        )}
      </div>
    </div>
  );
}

function ModeButton({ active, disabled = false, onClick, children }: { active: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "rounded-[3px] px-2 py-1 text-[12px] font-medium",
        active ? "bg-raised text-ink" : "text-ink-3 hover:text-ink-2",
        disabled && "cursor-not-allowed opacity-45 hover:text-ink-3",
      )}
    >
      {children}
    </button>
  );
}

interface BoxState {
  visible: boolean;
  severity: Severity | undefined;
  emphasized: boolean;
}

/** Wraps a value on the page with its extraction box. */
function Field({ field, state, children }: { field: DocField; state: BoxState; children: ReactNode }) {
  const color = state.severity ? SEVERITY[state.severity].paper : "var(--color-paper-accent)";
  return (
    <span data-field={field} className="relative inline-block" title={fieldKey(field)}>
      {children}
      {state.visible ? (
        <span
          aria-hidden
          className="pointer-events-none absolute -inset-x-[5px] -inset-y-[3px] rounded-[2px] transition-[border-width,background-color,box-shadow] duration-150"
          style={{
            border: `${state.emphasized ? 2 : 1}px solid ${color}`,
            opacity: state.severity ? 1 : 0.5,
            backgroundColor: state.severity ? `color-mix(in oklab, ${color} ${state.emphasized ? 16 : 7}%, transparent)` : "transparent",
            boxShadow: state.emphasized ? `0 0 0 4px color-mix(in oklab, ${color} 18%, transparent)` : undefined,
          }}
        >
          {state.severity ? (
            <span
              className="absolute bottom-full left-[-1px] mb-px whitespace-nowrap rounded-t-[2px] px-1 font-mono text-[8.5px] leading-[13px] text-white"
              style={{ backgroundColor: color }}
            >
              {fieldKey(field)}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}

function Paper({ invoice, organization, box }: { invoice: InvoiceRecord; organization: Organization; box: (field: DocField) => BoxState }) {
  const money = invoice.financial_summary;
  const currency = money.currency;
  const amount = (value: string | null) => (value === null ? "—" : formatAmount(value, currency));
  const duplicate = invoice.anomalies.find((a) => a.type === "DUPLICATE_FILE_HASH" && a.status !== "DISMISSED");
  const original = (duplicate?.detected_value?.duplicate_of as { invoice_number: string | null; submitted_at: string }[] | undefined)?.[0];
  const taxRate =
    money.subtotal && !isZero(money.tax) && !isZero(money.subtotal)
      ? `${((Number(money.tax) / Number(money.subtotal)) * 100).toFixed(2).replace(/\.?0+$/, "")}%`
      : null;

  return (
    <article
      aria-label={`Extracted fields of ${invoice.document.filename}`}
      className="relative overflow-hidden bg-paper px-12 pb-12 pt-11 font-sans text-[11px] leading-[1.55] text-paper-ink shadow-[0_1px_0_rgba(0,0,0,0.5),0_18px_48px_rgba(0,0,0,0.5)]"
    >
      <header className="flex justify-between gap-8">
        <div className="min-w-0">
          <Field field="vendor" state={box("vendor")}>
            <p className="font-letterhead text-[19px] font-bold leading-tight">{invoice.vendor.name ?? "Vendor not found"}</p>
          </Field>
          <p className="mt-2 text-paper-muted">
            Tax ID{" "}
            <Field field="tax_id" state={box("tax_id")}>
              <span className="font-mono text-paper-ink">{invoice.vendor.tax_id ?? "—"}</span>
            </Field>
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-label text-[24px] font-semibold uppercase leading-none tracking-[0.22em]">Invoice</p>
          <dl className="mt-3 grid grid-cols-[auto_auto] justify-end gap-x-4 gap-y-1">
            <dt className="text-paper-muted">Invoice no.</dt>
            <dd>
              <Field field="invoice_number" state={box("invoice_number")}>
                <span className="font-mono">{invoice.invoice_number ?? "—"}</span>
              </Field>
            </dd>
            <dt className="text-paper-muted">Date</dt>
            <dd>
              <Field field="invoice_date" state={box("invoice_date")}>
                {invoice.invoice_date ? formatDate(invoice.invoice_date) : "—"}
              </Field>
            </dd>
            <dt className="text-paper-muted">Due</dt>
            <dd>
              <Field field="due_date" state={box("due_date")}>
                {invoice.due_date ? formatDate(invoice.due_date) : "—"}
              </Field>
            </dd>
          </dl>
        </div>
      </header>

      <section className="mt-8">
        <p className="font-label text-[9.5px] font-semibold uppercase tracking-[0.14em] text-paper-muted">Bill to</p>
        <p>{organization.legal_name ?? organization.name}</p>
      </section>

      <table className="mt-7 w-full border-collapse">
        <thead>
          <tr className="border-b-2 border-paper-ink text-left font-label text-[9.5px] font-semibold uppercase tracking-[0.12em]">
            <th className="py-1.5 pr-2 font-semibold">Description</th>
            <th className="w-12 py-1.5 text-right font-semibold">Qty</th>
            <th className="w-24 py-1.5 text-right font-semibold">Unit price</th>
            <th className="w-28 py-1.5 text-right font-semibold">Amount</th>
          </tr>
        </thead>
        <tbody>
          {invoice.line_items.length === 0 ? (
            <tr className="border-b border-paper-line">
              <td colSpan={4} className="py-3 text-center text-paper-muted">
                No line items were extracted.
              </td>
            </tr>
          ) : (
            invoice.line_items.map((line) => (
              <tr key={line.line_number} className="border-b border-paper-line">
                <td className="py-2 pr-2">{line.description ?? "—"}</td>
                <td className="py-2 text-right font-mono">{Number(line.quantity).toLocaleString("en-US", { maximumFractionDigits: 4 })}</td>
                <td className="py-2 text-right font-mono">
                  <Field field={`line:${line.line_number}`} state={box(`line:${line.line_number}`)}>
                    {formatAmount(line.unit_price, currency)}
                  </Field>
                </td>
                <td className="py-2 text-right font-mono">{formatAmount(line.line_total, currency)}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      <dl className="ml-auto mt-4 grid w-[250px] grid-cols-[1fr_auto] gap-x-4 gap-y-1">
        <dt className="text-paper-muted">Subtotal</dt>
        <dd className="text-right font-mono">
          <Field field="subtotal" state={box("subtotal")}>
            {amount(money.subtotal)}
          </Field>
        </dd>
        <dt className="text-paper-muted">Tax{taxRate ? ` (${taxRate})` : ""}</dt>
        <dd className="text-right font-mono">
          <Field field="tax" state={box("tax")}>
            {amount(money.tax)}
          </Field>
        </dd>
        {!isZero(money.shipping) ? (
          <>
            <dt className="text-paper-muted">Shipping</dt>
            <dd className="text-right font-mono">
              <Field field="shipping" state={box("shipping")}>
                {amount(money.shipping)}
              </Field>
            </dd>
          </>
        ) : null}
        {!isZero(money.discount) ? (
          <>
            <dt className="text-paper-muted">Discount</dt>
            <dd className="text-right font-mono">
              <Field field="discount" state={box("discount")}>
                −{amount(money.discount)}
              </Field>
            </dd>
          </>
        ) : null}
        <dt className="mt-1 border-t-2 border-paper-ink pt-1.5 font-semibold">Total due ({currency})</dt>
        <dd className="mt-1 border-t-2 border-paper-ink pt-1.5 text-right font-mono text-[13px] font-semibold">
          <Field field="total" state={box("total")}>
            {amount(money.total)}
          </Field>
        </dd>
      </dl>

      {duplicate ? (
        <Stamp tone="var(--color-paper-critical)" className="right-9 top-[112px] rotate-[-9deg]">
          <span className="block text-[25px] tracking-[0.2em]">Duplicate</span>
          {original ? (
            <span className="block text-[9px] tracking-[0.12em]">
              of {original.invoice_number ?? "earlier file"} · rcvd {formatDate(original.submitted_at.slice(0, 10))}
            </span>
          ) : null}
        </Stamp>
      ) : invoice.status === "REJECTED" ? (
        <Stamp tone="var(--color-paper-critical)" className="right-12 top-[120px] rotate-[-7deg]">
          <span className="block text-[25px] tracking-[0.2em]">Rejected</span>
        </Stamp>
      ) : null}
    </article>
  );
}

/** A rubber stamp, as an AP clerk would have struck on the paper original. */
function Stamp({ tone, className, children }: { tone: string; className?: string; children: ReactNode }) {
  return (
    <div
      aria-hidden
      className={cn("pointer-events-none absolute rounded-[4px] px-3 py-1 text-center font-label font-semibold uppercase leading-tight opacity-80 mix-blend-multiply", className)}
      style={{ color: tone, border: `3px double ${tone}` }}
    >
      {children}
    </div>
  );
}
