import type { InvoiceRecord } from "./types";

/** "Invoice #1042" — how invoices are named in toasts, dialogs and headings. */
export function invoiceLabel(invoice: Pick<InvoiceRecord, "invoice_number" | "vendor">): string {
  if (invoice.invoice_number) return `Invoice #${invoice.invoice_number}`;
  return invoice.vendor.name ? `Invoice from ${invoice.vendor.name}` : "Invoice";
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export const plural = (count: number, one: string, many = `${one}s`) => `${count.toLocaleString()} ${count === 1 ? one : many}`;
