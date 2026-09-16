/** Filtering, sorting and totals for the Approved Bills archive. Pure functions over the ledger. */
import { DAY } from "@/lib/dates";
import { convert, sumAmounts } from "@/lib/money";
import type { Amount, InvoiceRecord } from "@/lib/types";

export type MethodFilter = "all" | "reviewer" | "automatic";
export type PeriodFilter = "all" | "7d" | "30d" | "90d" | "ytd";
export type ArchiveSortKey = "approved" | "invoice_number" | "vendor" | "invoice_date" | "total" | "confidence";
export interface ArchiveSort {
  key: ArchiveSortKey;
  dir: "asc" | "desc";
}

export const PERIODS: { value: PeriodFilter; label: string }[] = [
  { value: "all", label: "All time" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "ytd", label: "Year to date" },
];

/** Start of the approval window, or null for all time. */
export function periodStart(period: PeriodFilter, now: number): number | null {
  switch (period) {
    case "all":
      return null;
    case "7d":
      return now - 7 * DAY;
    case "30d":
      return now - 30 * DAY;
    case "90d":
      return now - 90 * DAY;
    case "ytd":
      return new Date(new Date(now).getFullYear(), 0, 1).getTime();
  }
}

/** When the invoice was approved (falls back to processing time for records without a stamp). */
export const approvedAt = (invoice: InvoiceRecord) => Date.parse(invoice.approval?.at ?? invoice.processed_at ?? invoice.created_at);

function matches(invoice: InvoiceRecord, query: string): boolean {
  return [
    invoice.invoice_number,
    invoice.vendor.name,
    invoice.vendor.tax_id,
    invoice.approval?.by?.name,
    invoice.document.filename,
    invoice.invoice_id,
  ].some((field) => field?.toLowerCase().includes(query));
}

export interface ArchiveFilters {
  query: string;
  method: MethodFilter;
  period: PeriodFilter;
}

export function filterArchive(invoices: readonly InvoiceRecord[], { query, method, period }: ArchiveFilters, now: number): InvoiceRecord[] {
  const needle = query.trim().toLowerCase();
  const from = periodStart(period, now);
  return invoices.filter(
    (invoice) =>
      invoice.status === "APPROVED" &&
      (method === "all" || invoice.approval?.method === method) &&
      (from === null || approvedAt(invoice) >= from) &&
      (!needle || matches(invoice, needle)),
  );
}

/** Nulls sort last in either direction; ties fall back to the most recent approval. */
export function sortArchive(invoices: readonly InvoiceRecord[], { key, dir }: ArchiveSort, currency: string): InvoiceRecord[] {
  const value = (invoice: InvoiceRecord): number | string | null => {
    switch (key) {
      case "approved":
        return approvedAt(invoice);
      case "invoice_number":
        return invoice.invoice_number;
      case "vendor":
        return invoice.vendor.name;
      case "invoice_date":
        return invoice.invoice_date;
      case "total":
        return invoice.financial_summary.total === null ? null : convert(invoice.financial_summary.total, invoice.financial_summary.currency, currency);
      case "confidence":
        return invoice.ai_confidence;
    }
  };
  const sign = dir === "asc" ? 1 : -1;
  const keyed = invoices.map((invoice) => ({ invoice, value: value(invoice), approved: approvedAt(invoice) }));
  keyed.sort((a, b) => {
    if (a.value === null || b.value === null) return a.value === b.value ? b.approved - a.approved : a.value === null ? 1 : -1;
    const order =
      typeof a.value === "number" && typeof b.value === "number"
        ? a.value - b.value
        : String(a.value).localeCompare(String(b.value), undefined, { numeric: true, sensitivity: "base" });
    return sign * order || b.approved - a.approved;
  });
  return keyed.map((item) => item.invoice);
}

/** Exact per-currency sums, most-used currency first. Never mixes currencies. */
export function totalsByCurrency(invoices: readonly InvoiceRecord[]): { currency: string; amount: Amount; count: number }[] {
  const groups = new Map<string, Amount[]>();
  for (const invoice of invoices) {
    const { currency, total } = invoice.financial_summary;
    if (total === null) continue;
    const group = groups.get(currency);
    if (group) group.push(total);
    else groups.set(currency, [total]);
  }
  return [...groups.entries()]
    .map(([currency, amounts]) => ({ currency, amount: sumAmounts(amounts), count: amounts.length }))
    .sort((a, b) => b.count - a.count || a.currency.localeCompare(b.currency));
}

export interface ArchiveSummary {
  count: number;
  /** in the organization's currency, other currencies at reference rates */
  value: number;
  reviewer: number;
  automatic: number;
  averageConfidence: number | null;
  /** findings a reviewer dismissed on the way to approval */
  overridden: number;
  /** of which HIGH or CRITICAL */
  overriddenBlocking: number;
  mixedCurrencies: boolean;
}

export function summarize(invoices: readonly InvoiceRecord[], currency: string): ArchiveSummary {
  let value = 0;
  let reviewer = 0;
  let confidenceSum = 0;
  let confidenceCount = 0;
  let overridden = 0;
  let overriddenBlocking = 0;
  let mixedCurrencies = false;
  for (const invoice of invoices) {
    const { total, currency: invoiceCurrency } = invoice.financial_summary;
    if (total !== null) value += convert(total, invoiceCurrency, currency);
    if (invoiceCurrency !== currency) mixedCurrencies = true;
    if (invoice.approval?.method === "reviewer") reviewer += 1;
    if (invoice.ai_confidence !== null) {
      confidenceSum += invoice.ai_confidence;
      confidenceCount += 1;
    }
    for (const anomaly of invoice.anomalies) {
      if (anomaly.status !== "DISMISSED") continue;
      overridden += 1;
      if (anomaly.severity === "HIGH" || anomaly.severity === "CRITICAL") overriddenBlocking += 1;
    }
  }
  return {
    count: invoices.length,
    value,
    reviewer,
    automatic: invoices.length - reviewer,
    averageConfidence: confidenceCount ? confidenceSum / confidenceCount : null,
    overridden,
    overriddenBlocking,
    mixedCurrencies,
  };
}
