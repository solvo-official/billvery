/** The verification checklist and audit trail behind an audit report. Pure functions. */
import { formatAmount, toUnits } from "@/lib/money";
import { ANOMALY_INFO, evaluateMath } from "@/lib/rules";
import type { Anomaly, AnomalyType, InvoiceRecord } from "@/lib/types";

export type CheckState = "passed" | "overridden" | "open" | "confirmed" | "not-run";

export interface Check {
  id: string;
  label: string;
  state: CheckState;
  detail: string;
  findings: Anomaly[];
}

const CHECKS: { id: string; label: string; types: AnomalyType[] }[] = [
  { id: "footing", label: "Arithmetic footing", types: ["MATH_TOTAL_MISMATCH"] },
  { id: "tax", label: "Tax rate", types: ["TAX_RATE_EXCEEDED"] },
  { id: "duplicates", label: "Duplicate detection", types: ["DUPLICATE_FILE_HASH", "DUPLICATE_INVOICE_NUMBER", "SIMILAR_INVOICE_NUMBER"] },
  { id: "vendor", label: "Vendor identity", types: ["VENDOR_TAX_ID_MISMATCH", "VENDOR_NAME_MISMATCH", "SIMILAR_VENDOR_NAME"] },
  { id: "pricing", label: "Unit pricing", types: ["PRICE_SPIKE"] },
  { id: "dates", label: "Invoice dates", types: ["FUTURE_INVOICE_DATE", "STALE_INVOICE_DATE"] },
  { id: "fields", label: "Required fields", types: ["MISSING_REQUIRED_FIELDS"] },
  { id: "extraction", label: "Extraction confidence", types: ["LOW_EXTRACTION_CONFIDENCE"] },
];

/** Effective tax as a percentage of the subtotal, or null when it can't be computed. */
export function effectiveTaxRate(invoice: InvoiceRecord): number | null {
  const { subtotal, tax } = invoice.financial_summary;
  if (subtotal === null || toUnits(subtotal) === 0n) return null;
  return (Number(tax) / Number(subtotal)) * 100;
}

function passedDetail(id: string, invoice: InvoiceRecord): Pick<Check, "state" | "detail"> {
  const currency = invoice.financial_summary.currency;
  switch (id) {
    case "footing": {
      const footing = evaluateMath(invoice.financial_summary);
      return footing
        ? { state: "passed", detail: `Subtotal + tax + shipping − discount = ${formatAmount(footing.expected, currency)} ${currency}` }
        : { state: "not-run", detail: "Subtotal or total wasn't on the document, so it couldn't be footed" };
    }
    case "tax": {
      const rate = effectiveTaxRate(invoice);
      return rate === null ? { state: "not-run", detail: "No subtotal to compare the tax against" } : { state: "passed", detail: `Effective rate ${rate.toFixed(2)}% of subtotal · no country-limit finding` };
    }
    case "duplicates":
      return { state: "passed", detail: "No matching file, invoice number or near-duplicate in this organization" };
    case "vendor":
      return invoice.vendor_on_file?.first_seen
        ? { state: "passed", detail: "New vendor: the vendor record was created by this invoice" }
        : invoice.vendor_on_file
          ? { state: "passed", detail: `Matches ${invoice.vendor_on_file.name} on file` }
          : { state: "not-run", detail: "Vendor name couldn't be read" };
    case "pricing":
      return { state: "passed", detail: "Every unit price is under 2.5× this organization's average" };
    case "dates":
      return { state: "passed", detail: "Issue date is neither in the future nor over a year old" };
    case "fields":
      return { state: "passed", detail: "Vendor, invoice number, issue date and total were all found" };
    case "extraction":
      return { state: "passed", detail: invoice.ai_confidence === null ? "Gemini reported no confidence" : `Gemini read it with ${invoice.ai_confidence}% confidence (threshold 90%)` };
    default:
      return { state: "passed", detail: "No finding" };
  }
}

export function buildChecks(invoice: InvoiceRecord, userName: (id: string | null) => string): Check[] {
  return CHECKS.map(({ id, label, types }) => {
    const findings = invoice.anomalies.filter((a) => types.includes(a.type));
    if (findings.length === 0) return { id, label, findings, ...passedDetail(id, invoice) };
    const names = [...new Set(findings.map((a) => ANOMALY_INFO[a.type].label))].join(", ");
    if (findings.some((a) => a.status === "CONFIRMED")) return { id, label, findings, state: "confirmed", detail: `${names} · confirmed by a reviewer` };
    if (findings.some((a) => a.status === "OPEN")) return { id, label, findings, state: "open", detail: `${names} · informational, left open` };
    const resolvers = [...new Set(findings.map((a) => userName(a.resolved_by)))].join(", ");
    return { id, label, findings, state: "overridden", detail: `${names} · dismissed by ${resolvers}` };
  });
}

export interface TrailEvent {
  id: string;
  at: string;
  kind: "received" | "extracted" | "flagged" | "dismissed" | "confirmed" | "approved";
  title: string;
  detail?: string;
  note?: string;
}

/** Everything that happened to the invoice, oldest first. Identical resolutions are grouped. */
export function buildTrail(invoice: InvoiceRecord, userName: (id: string | null) => string): TrailEvent[] {
  const events: TrailEvent[] = [
    {
      id: "received",
      at: invoice.created_at,
      kind: "received",
      title: invoice.source === "upload" ? `Uploaded by ${invoice.submitted_by?.name ?? "a user"}` : "Submitted through the API",
      detail: invoice.document.filename,
    },
  ];
  if (invoice.processed_at) {
    events.push({
      id: "extracted",
      at: invoice.processed_at,
      kind: "extracted",
      title: "Extracted and audited",
      detail: `Gemini confidence ${invoice.ai_confidence ?? "—"}% · ${invoice.anomalies.length === 0 ? "no rule raised a finding" : `${invoice.anomalies.length} finding${invoice.anomalies.length === 1 ? "" : "s"} raised`}`,
    });
  }

  const groups = new Map<string, Anomaly[]>();
  for (const anomaly of invoice.anomalies) {
    if (anomaly.status === "OPEN" || !anomaly.resolved_at) continue;
    const key = `${anomaly.status}|${anomaly.resolved_by}|${anomaly.resolved_at.slice(0, 19)}|${anomaly.resolution_note ?? ""}`;
    groups.set(key, [...(groups.get(key) ?? []), anomaly]);
  }
  for (const [key, anomalies] of groups) {
    const first = anomalies[0]!;
    const verb = first.status === "DISMISSED" ? "dismissed" : "confirmed";
    events.push({
      id: key,
      at: first.resolved_at!,
      kind: first.status === "DISMISSED" ? "dismissed" : "confirmed",
      title: `${userName(first.resolved_by)} ${verb} ${anomalies.length === 1 ? ANOMALY_INFO[first.type].label.toLowerCase() : `${anomalies.length} flags`}`,
      detail: anomalies.length > 1 ? anomalies.map((a) => ANOMALY_INFO[a.type].label).join(" · ") : undefined,
      note: first.resolution_note ?? undefined,
    });
  }

  if (invoice.approval) {
    events.push({
      id: "approved",
      at: invoice.approval.at,
      kind: "approved",
      title: invoice.approval.method === "automatic" ? "Approved automatically by the rule engine" : `Approved by ${invoice.approval.by?.name ?? "a reviewer"}`,
      detail: invoice.approval.method === "automatic" ? "No HIGH or CRITICAL finding and confidence at or above 90%" : "Moved to Approved Bills",
    });
  }
  // Stable: same-instant events keep their narrative order (received → extracted → decisions → approval).
  return events.map((event, index) => ({ event, index })).sort((a, b) => Date.parse(a.event.at) - Date.parse(b.event.at) || a.index - b.index).map(({ event }) => event);
}
