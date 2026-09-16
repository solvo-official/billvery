/**
 * CSV and JSON exports of audited invoices, generated in the browser from the ledger the
 * console already holds. CSV follows RFC 4180 (CRLF rows, quoted fields, doubled quotes), starts
 * with a UTF-8 BOM so Excel reads non-ASCII vendor names correctly, and neutralizes spreadsheet
 * formulas: a text cell starting with = + - @ tab or CR is prefixed with an apostrophe, because
 * vendor names and descriptions come from uploaded documents and may be hostile.
 */
import { toIsoDate } from "./dates";
import type { InvoiceRecord, Organization } from "./types";

type Cell = string | number | null | undefined;

interface Column<Row> {
  header: string;
  /** number cells are written as-is when they look numeric; everything else gets the formula guard */
  kind: "text" | "number";
  value: (row: Row) => Cell;
}

const FORMULA_START = /^[=+\-@\t\r]/;
const NUMERIC = /^-?\d+(?:\.\d+)?$/;

export function csvCell(value: Cell, kind: "text" | "number" = "text"): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  if (!(kind === "number" && NUMERIC.test(text)) && FORMULA_START.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) || text !== text.trim() ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv<Row>(rows: readonly Row[], columns: readonly Column<Row>[]): string {
  const lines = [
    columns.map((column) => csvCell(column.header)).join(","),
    ...rows.map((row) => columns.map((column) => csvCell(column.value(row), column.kind)).join(",")),
  ];
  return `﻿${lines.join("\r\n")}\r\n`;
}

const count = (invoice: InvoiceRecord, status: "OPEN" | "DISMISSED" | "CONFIRMED") => invoice.anomalies.filter((a) => a.status === status).length;

/** One row per invoice: the ledger view of an approval. */
const INVOICE_COLUMNS: Column<InvoiceRecord>[] = [
  { header: "Invoice ID", kind: "text", value: (i) => i.invoice_id },
  { header: "Invoice number", kind: "text", value: (i) => i.invoice_number },
  { header: "Vendor", kind: "text", value: (i) => i.vendor.name },
  { header: "Vendor tax ID", kind: "text", value: (i) => i.vendor.tax_id },
  { header: "Issue date", kind: "text", value: (i) => i.invoice_date },
  { header: "Due date", kind: "text", value: (i) => i.due_date },
  { header: "Currency", kind: "text", value: (i) => i.financial_summary.currency },
  { header: "Subtotal", kind: "number", value: (i) => i.financial_summary.subtotal },
  { header: "Tax", kind: "number", value: (i) => i.financial_summary.tax },
  { header: "Shipping", kind: "number", value: (i) => i.financial_summary.shipping },
  { header: "Discount", kind: "number", value: (i) => i.financial_summary.discount },
  { header: "Total", kind: "number", value: (i) => i.financial_summary.total },
  { header: "Status", kind: "text", value: (i) => i.status },
  { header: "Approved at", kind: "text", value: (i) => i.approval?.at },
  { header: "Approved by", kind: "text", value: (i) => (i.approval ? (i.approval.by?.name ?? "Rule engine") : null) },
  { header: "Approval method", kind: "text", value: (i) => i.approval?.method },
  { header: "AI confidence", kind: "number", value: (i) => i.ai_confidence },
  { header: "Flags raised", kind: "number", value: (i) => i.anomalies.length },
  { header: "Flags dismissed", kind: "number", value: (i) => count(i, "DISMISSED") },
  { header: "Flags confirmed", kind: "number", value: (i) => count(i, "CONFIRMED") },
  { header: "Flag types", kind: "text", value: (i) => [...new Set(i.anomalies.map((a) => a.type))].join("; ") },
  { header: "Source", kind: "text", value: (i) => i.source },
  { header: "Submitted by", kind: "text", value: (i) => i.submitted_by?.name },
  { header: "Received at", kind: "text", value: (i) => i.created_at },
  { header: "Document", kind: "text", value: (i) => i.document.filename },
  { header: "Document SHA-256", kind: "text", value: (i) => i.document.sha256 },
];

interface LineRow {
  invoice: InvoiceRecord;
  line: InvoiceRecord["line_items"][number];
}

const LINE_COLUMNS: Column<LineRow>[] = [
  { header: "Invoice number", kind: "text", value: (r) => r.invoice.invoice_number },
  { header: "Vendor", kind: "text", value: (r) => r.invoice.vendor.name },
  { header: "Currency", kind: "text", value: (r) => r.invoice.financial_summary.currency },
  { header: "Line", kind: "number", value: (r) => r.line.line_number },
  { header: "Description", kind: "text", value: (r) => r.line.description },
  { header: "Quantity", kind: "number", value: (r) => r.line.quantity },
  { header: "Unit price", kind: "number", value: (r) => r.line.unit_price },
  { header: "Line total", kind: "number", value: (r) => r.line.line_total },
];

export const invoicesToCsv = (invoices: readonly InvoiceRecord[]) => toCsv(invoices, INVOICE_COLUMNS);

export const lineItemsToCsv = (invoice: InvoiceRecord) => toCsv(invoice.line_items.map((line) => ({ invoice, line })), LINE_COLUMNS);

/** The full audit records, with enough context to stand alone as an evidence file. */
export function invoicesToJson(invoices: readonly InvoiceRecord[], organization: Pick<Organization, "id" | "name">): string {
  return JSON.stringify(
    {
      format: "invoice-auditor.audit-export/v1",
      generated_at: new Date().toISOString(),
      organization: { id: organization.id, name: organization.name },
      count: invoices.length,
      invoices,
    },
    null,
    2,
  );
}

const safe = (text: string) => text.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "invoice";

export type ExportFormat = "csv" | "json";

/** Hands the browser a generated file. */
export function downloadFile(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export function exportInvoices(invoices: readonly InvoiceRecord[], format: ExportFormat, organization: Pick<Organization, "id" | "name">): string {
  const filename = `approved-bills-${toIsoDate(new Date())}-${invoices.length}.${format}`;
  if (format === "csv") downloadFile(filename, invoicesToCsv(invoices), "text/csv;charset=utf-8");
  else downloadFile(filename, invoicesToJson(invoices, organization), "application/json");
  return filename;
}

/** One invoice: the full audit report as JSON, or its line items as CSV. */
export function exportInvoice(invoice: InvoiceRecord, format: ExportFormat, organization: Pick<Organization, "id" | "name">): string {
  const name = safe(invoice.invoice_number ?? invoice.invoice_id.slice(0, 8));
  if (format === "csv") {
    const filename = `line-items-${name}.csv`;
    downloadFile(filename, lineItemsToCsv(invoice), "text/csv;charset=utf-8");
    return filename;
  }
  const filename = `audit-report-${name}.json`;
  downloadFile(filename, invoicesToJson([invoice], organization), "application/json");
  return filename;
}
