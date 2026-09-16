/**
 * Client-side mirror of the backend's rule semantics. `decideInvoiceStatus` and
 * `evaluateMath` are line-for-line ports of anomaly_detection.py so the UI can preview the
 * outcome of a resolution before committing it.
 */
import { fromUnits, toUnits } from "./money";
import type {
  Anomaly,
  AnomalyType,
  DocField,
  FinancialSummary,
  InvoiceRecord,
  Severity,
  SettledStatus,
} from "./types";

export const SEVERITY_RANK: Record<Severity, number> = { LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4 };
export const SEVERITIES: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

/** HIGH and CRITICAL findings hold an invoice for review. */
export const isBlocking = (severity: Severity) => SEVERITY_RANK[severity] >= SEVERITY_RANK.HIGH;

export function decideInvoiceStatus(anomalies: readonly Pick<Anomaly, "severity" | "status">[]): SettledStatus {
  const blocking = anomalies.filter((a) => isBlocking(a.severity));
  if (blocking.some((a) => a.status === "CONFIRMED")) return "REJECTED";
  if (blocking.some((a) => a.status === "OPEN")) return "NEEDS_REVIEW";
  return "APPROVED";
}

export const openAnomalies = (invoice: InvoiceRecord) => invoice.anomalies.filter((a) => a.status === "OPEN");

export const bySeverity = (a: Anomaly, b: Anomaly) =>
  SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] || a.type.localeCompare(b.type);

/** True when the engine approved the invoice without a human touching it. */
export const isStraightThrough = (invoice: InvoiceRecord) =>
  invoice.status === "APPROVED" && invoice.anomalies.every((a) => a.status === "OPEN");

// --- Footing (MATH_TOTAL_MISMATCH) -------------------------------------------------------

export interface Footing {
  expected: string;
  difference: string;
  percent: number | null;
  severity: Severity | null;
}

/** abs((subtotal + tax + shipping - discount) - total) > tolerance; severity by % of total. */
export function evaluateMath(summary: FinancialSummary, tolerance = "0.05"): Footing | null {
  if (summary.subtotal === null || summary.total === null) return null;
  const expected =
    toUnits(summary.subtotal) + toUnits(summary.tax) + toUnits(summary.shipping) - toUnits(summary.discount);
  const total = toUnits(summary.total);
  const diff = expected > total ? expected - total : total - expected;
  if (diff <= toUnits(tolerance)) {
    return { expected: fromUnits(expected), difference: fromUnits(diff), percent: 0, severity: null };
  }
  const percent = total === 0n ? null : (Number(diff) / Math.abs(Number(total))) * 100;
  const severity: Severity = percent === null ? "HIGH" : percent < 1 ? "LOW" : percent <= 5 ? "MEDIUM" : "HIGH";
  return { expected: fromUnits(expected), difference: fromUnits(diff), percent, severity };
}

// --- Risk score --------------------------------------------------------------------------

const SEVERITY_WEIGHT: Record<Severity, number> = { LOW: 0.12, MEDIUM: 0.35, HIGH: 0.7, CRITICAL: 0.95 };

/**
 * 0-100. A noisy-OR over the findings still in play: each open finding contributes its
 * severity weight x rule confidence, a confirmed one counts as certain, a dismissed one not
 * at all; extraction uncertainty adds a small base. Client-side heuristic, not an API field.
 */
export function riskScore(invoice: InvoiceRecord): number | null {
  if (invoice.status === "PROCESSING") return null;
  const uncertainty = invoice.ai_confidence === null ? 0.2 : Math.max(0, (100 - invoice.ai_confidence) / 100) * 0.5;
  let clean = 1 - uncertainty;
  for (const anomaly of invoice.anomalies) {
    if (anomaly.status === "DISMISSED") continue;
    const weight = anomaly.status === "CONFIRMED" ? 1 : SEVERITY_WEIGHT[anomaly.severity] * (anomaly.confidence / 100);
    clean *= 1 - weight;
  }
  return Math.round((1 - clean) * 100);
}

export type RiskBand = "low" | "moderate" | "high" | "severe";

export function riskBand(score: number): RiskBand {
  if (score >= 80) return "severe";
  if (score >= 50) return "high";
  if (score >= 25) return "moderate";
  return "low";
}

// --- Catalog -----------------------------------------------------------------------------

/** "fraud" findings are about who is billing; "quality" findings are about the document. */
export type AnomalyClass = "fraud" | "quality";

export const ANOMALY_INFO: Record<AnomalyType, { label: string; kind: AnomalyClass; rule: string }> = {
  DUPLICATE_FILE_HASH: {
    label: "Duplicate file",
    kind: "fraud",
    rule: "Byte-identical file already submitted to this organization",
  },
  DUPLICATE_INVOICE_NUMBER: {
    label: "Duplicate invoice number",
    kind: "fraud",
    rule: "Same number from the same vendor within 90 days",
  },
  SIMILAR_INVOICE_NUMBER: {
    label: "Near-duplicate invoice number",
    kind: "fraud",
    rule: "Same vendor, date and total with an almost identical number",
  },
  VENDOR_TAX_ID_MISMATCH: {
    label: "Tax ID differs from vendor on file",
    kind: "fraud",
    rule: "Known vendor name, different tax registration number",
  },
  VENDOR_NAME_MISMATCH: {
    label: "Vendor name differs from tax ID owner",
    kind: "fraud",
    rule: "Known tax ID, different vendor name",
  },
  SIMILAR_VENDOR_NAME: {
    label: "Look-alike vendor",
    kind: "fraud",
    rule: "New vendor name closely resembles an existing vendor",
  },
  MATH_TOTAL_MISMATCH: {
    label: "Total doesn't foot",
    kind: "quality",
    rule: "subtotal + tax + shipping − discount ≠ total (tolerance 0.05)",
  },
  PRICE_SPIKE: {
    label: "Unit price spike",
    kind: "fraud",
    rule: "Unit price ≥ 2.5× this organization's average for the item",
  },
  FUTURE_INVOICE_DATE: { label: "Future-dated invoice", kind: "quality", rule: "Issue date after tomorrow" },
  STALE_INVOICE_DATE: { label: "Stale invoice", kind: "quality", rule: "Issue date more than 365 days ago" },
  MISSING_REQUIRED_FIELDS: {
    label: "Missing required fields",
    kind: "quality",
    rule: "Vendor, invoice number, issue date or total not found",
  },
  TAX_RATE_EXCEEDED: {
    label: "Tax above country limit",
    kind: "quality",
    rule: "Tax exceeds subtotal × configured country limit",
  },
  LOW_EXTRACTION_CONFIDENCE: {
    label: "Low extraction confidence",
    kind: "quality",
    rule: "Gemini confidence below the 90% auto-approval threshold",
  },
};

const MISSING_FIELD_TO_DOC: Record<string, DocField> = {
  vendor_name: "vendor",
  invoice_number: "invoice_number",
  invoice_date: "invoice_date",
  total_amount: "total",
};

/** The regions of the source document a finding points at. */
export function anomalyFields(anomaly: Anomaly, invoice: InvoiceRecord): DocField[] {
  const detected = anomaly.detected_value ?? {};
  switch (anomaly.type) {
    case "MATH_TOTAL_MISMATCH":
      return ["subtotal", "tax", "shipping", "discount", "total"];
    case "DUPLICATE_INVOICE_NUMBER":
    case "SIMILAR_INVOICE_NUMBER":
      return ["invoice_number"];
    case "DUPLICATE_FILE_HASH":
      return ["invoice_number", "total"];
    case "VENDOR_TAX_ID_MISMATCH":
      return ["tax_id"];
    case "VENDOR_NAME_MISMATCH":
    case "SIMILAR_VENDOR_NAME":
      return ["vendor"];
    case "PRICE_SPIKE":
      return [`line:${Number(detected.line_number ?? 1)}`];
    case "FUTURE_INVOICE_DATE":
    case "STALE_INVOICE_DATE":
      return ["invoice_date"];
    case "TAX_RATE_EXCEEDED":
      return ["subtotal", "tax"];
    case "MISSING_REQUIRED_FIELDS":
      return ((detected.missing_fields as string[] | undefined) ?? []).flatMap((f) => MISSING_FIELD_TO_DOC[f] ?? []);
    case "LOW_EXTRACTION_CONFIDENCE":
      // Confidence is reported for the whole document, so every extracted value is in question.
      return invoice.vendor.confidence !== null && invoice.vendor.confidence < 90 ? ["vendor", "total"] : ["total"];
  }
}

/** Tax IDs compare as upper-case alphanumerics, the way the backend stores them. */
export const normalizeTaxId = (taxId: string) => taxId.replace(/[^0-9a-z]/gi, "").toUpperCase();
