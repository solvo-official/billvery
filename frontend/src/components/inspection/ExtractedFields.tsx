import { Check, X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { DAY, formatDate } from "@/lib/dates";
import { formatAmount, isZero } from "@/lib/money";
import { evaluateMath, normalizeTaxId } from "@/lib/rules";
import type { InvoiceRecord } from "@/lib/types";

/** Highlights the characters that differ between a printed identifier and the one on file. */
export function IdentifierDiff({ value, reference, tone = "rejected" }: { value: string; reference: string; tone?: "rejected" | "neutral" }) {
  const sameLength = value.length === reference.length;
  return (
    <span className="figure">
      {value.split("").map((char, index) => {
        const differs = !sameLength || char !== reference[index];
        return (
          <span
            key={index}
            className={cn(differs && (tone === "rejected" ? "rounded-[2px] bg-rejected/20 text-rejected" : "rounded-[2px] bg-ink-3/20 text-ink"))}
          >
            {char}
          </span>
        );
      })}
    </span>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-[12px] text-ink-3">{label}</dt>
      <dd className="min-w-0 text-[13px] text-ink">{children}</dd>
    </>
  );
}

export function ExtractedFields({ invoice, spikedLines }: { invoice: InvoiceRecord; spikedLines: Map<number, number> }) {
  const money = invoice.financial_summary;
  const currency = money.currency;
  const onFile = invoice.vendor_on_file;
  const taxIdOnFile = onFile?.tax_id ?? null;
  const printedTaxId = invoice.vendor.tax_id;
  // The vendor master stores tax IDs normalized; compare like with like.
  const printedNormalized = printedTaxId ? normalizeTaxId(printedTaxId) : null;
  const taxIdDiffers = printedNormalized !== null && taxIdOnFile !== null && printedNormalized !== taxIdOnFile;
  const footing = evaluateMath(money);
  const termsDays =
    invoice.invoice_date && invoice.due_date
      ? Math.round((new Date(invoice.due_date).getTime() - new Date(invoice.invoice_date).getTime()) / DAY)
      : null;

  return (
    <section aria-labelledby="extracted-heading" className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id="extracted-heading" className="eyebrow">
          Extracted fields
        </h3>
        <span className="text-[11px] text-ink-3">
          Gemini · <span className="figure text-ink-2">{invoice.ai_confidence ?? "—"}%</span> overall confidence
        </span>
      </div>

      <dl className="grid grid-cols-[104px_minmax(0,1fr)] gap-x-3 gap-y-2">
        <Row label="Vendor">
          <span className="block truncate">{invoice.vendor.name ?? "—"}</span>
          {onFile === null ? (
            <span className="text-[12px] text-review">No vendor record: the name couldn't be read</span>
          ) : onFile.first_seen ? (
            <span className="text-[12px] text-ink-3">New vendor: first invoice from them</span>
          ) : onFile.name !== invoice.vendor.name ? (
            <span className="text-[12px] text-ink-3">
              On file as <span className="text-ink-2">{onFile.name}</span>
            </span>
          ) : null}
        </Row>
        <Row label="Tax ID">
          {taxIdDiffers ? (
            <span className="flex flex-col gap-0.5">
              <span className="figure">{printedTaxId}</span>
              <span className="text-[12px] text-ink-3">
                <IdentifierDiff value={printedNormalized!} reference={taxIdOnFile!} /> vs on file{" "}
                <IdentifierDiff value={taxIdOnFile!} reference={printedNormalized!} tone="neutral" />
              </span>
            </span>
          ) : (
            <span className="flex items-center gap-1.5">
              <span className="figure">{printedTaxId ?? "—"}</span>
              {printedNormalized && taxIdOnFile && !onFile?.first_seen ? (
                <Check className="size-3.5 text-approved" aria-label="Matches the vendor on file" />
              ) : null}
            </span>
          )}
        </Row>
        <Row label="Invoice no.">
          <span className="figure">{invoice.invoice_number ?? "—"}</span>
        </Row>
        <Row label="Issue date">{invoice.invoice_date ? formatDate(invoice.invoice_date) : "—"}</Row>
        <Row label="Due date">
          {invoice.due_date ? formatDate(invoice.due_date) : "—"}
          {termsDays !== null && termsDays >= 0 ? <span className="text-ink-3"> · net {termsDays} days</span> : null}
        </Row>
        <Row label="Currency">
          <span className="figure">{currency}</span>
        </Row>
        {invoice.submitted_by ? <Row label="Uploaded by">{invoice.submitted_by.name}</Row> : null}
      </dl>

      <div className="overflow-x-auto rounded-[5px] border border-line">
        <table className="w-full min-w-[380px] border-collapse text-[12px]">
          <thead>
            <tr className="border-b border-line bg-raised/50 text-left">
              <th className="eyebrow w-6 py-1.5 pl-2.5 font-semibold">#</th>
              <th className="eyebrow py-1.5 pl-2 font-semibold">Line item</th>
              <th className="eyebrow w-12 py-1.5 text-right font-semibold">Qty</th>
              <th className="eyebrow w-24 py-1.5 text-right font-semibold">Unit</th>
              <th className="eyebrow w-28 py-1.5 pr-2.5 text-right font-semibold">Amount</th>
            </tr>
          </thead>
          <tbody>
            {invoice.line_items.map((line) => {
              const spike = spikedLines.get(line.line_number);
              return (
                <tr key={line.line_number} className={cn("border-b border-line last:border-b-0", spike && "bg-sev-high/[0.07]")}>
                  <td className="figure py-1.5 pl-2.5 text-ink-3">{line.line_number}</td>
                  <td className="py-1.5 pl-2 text-ink-2">
                    {line.description}
                    {spike ? <span className="figure ml-1.5 rounded-[3px] bg-sev-high/15 px-1 text-[11px] text-sev-high">{spike.toFixed(1)}× avg</span> : null}
                  </td>
                  <td className="figure py-1.5 text-right text-ink-2">{Number(line.quantity).toLocaleString("en-US")}</td>
                  <td className="figure py-1.5 text-right text-ink-2">{formatAmount(line.unit_price, currency)}</td>
                  <td className="figure py-1.5 pr-2.5 text-right text-ink">{formatAmount(line.line_total, currency)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-end justify-between gap-4">
        <div className="text-[12px]">
          {footing?.severity ? (
            <span className="flex items-center gap-1.5 text-rejected">
              <X className="size-3.5" aria-hidden />
              Doesn't foot · off by <span className="figure">{formatAmount(footing.difference, currency)}</span>
            </span>
          ) : footing ? (
            <span className="flex items-center gap-1.5 text-approved">
              <Check className="size-3.5" aria-hidden />
              Foots to the printed total
            </span>
          ) : (
            <span className="text-ink-3">Can't foot: subtotal or total missing</span>
          )}
        </div>
        <dl className="figure grid grid-cols-[auto_auto] gap-x-5 gap-y-0.5 text-right text-[12px]">
          <dt className="font-sans text-ink-3">Subtotal</dt>
          <dd className="text-ink-2">{money.subtotal ? formatAmount(money.subtotal, currency) : "—"}</dd>
          <dt className="font-sans text-ink-3">Tax</dt>
          <dd className="text-ink-2">{formatAmount(money.tax, currency)}</dd>
          {!isZero(money.shipping) ? (
            <>
              <dt className="font-sans text-ink-3">Shipping</dt>
              <dd className="text-ink-2">{formatAmount(money.shipping, currency)}</dd>
            </>
          ) : null}
          {!isZero(money.discount) ? (
            <>
              <dt className="font-sans text-ink-3">Discount</dt>
              <dd className="text-ink-2">−{formatAmount(money.discount, currency)}</dd>
            </>
          ) : null}
          <dt className="border-t border-line-strong pt-1 font-sans text-ink">Total</dt>
          <dd className="border-t border-line-strong pt-1 text-[14px] font-medium text-ink">
            {money.total ? formatAmount(money.total, currency) : "—"}
            <span className="ml-1 text-[11px] text-ink-3">{currency}</span>
          </dd>
        </dl>
      </div>
    </section>
  );
}
