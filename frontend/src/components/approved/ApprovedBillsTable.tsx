import { Bot, Braces, Check, Copy, ExternalLink, FileSpreadsheet, FileText, LoaderCircle, MoreHorizontal } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Avatar, Money } from "@/components/audit/status";
import { SortHeader } from "@/components/feed/InvoiceTable";
import { SkeletonRow } from "@/components/shell/DashboardSkeleton";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatDay, relativeTime } from "@/lib/dates";
import type { ExportFormat } from "@/lib/export";
import type { InvoiceRecord } from "@/lib/types";
import type { ArchiveSort, ArchiveSortKey } from "./archive";
import { ConfidenceBadge } from "./ConfidenceBadge";

const ARRIVAL_HIGHLIGHT_MS = 3_000;
const COLUMNS = 8;

interface ApprovedBillsTableProps {
  rows: InvoiceRecord[];
  /** rows to hold while the archive loads, so the table keeps its height */
  skeletonRows: number;
  sort: ArchiveSort;
  onSort: (key: ArchiveSortKey) => void;
  isSelected: (invoiceId: string) => boolean;
  onToggle: (invoiceId: string, on: boolean) => void;
  /** header checkbox: select or clear every row on this page */
  onTogglePage: (on: boolean) => void;
  onOpenReport: (invoiceId: string) => void;
  onExport: (invoice: InvoiceRecord, format: ExportFormat) => void;
  touched: Record<string, number>;
  pending: Record<string, true>;
  reportId: string | null;
  now: number;
  caption: string;
  empty: ReactNode;
}

export function ApprovedBillsTable(props: ApprovedBillsTableProps) {
  const { rows, skeletonRows, sort, onSort, isSelected, onTogglePage, caption, empty } = props;
  const selectedOnPage = rows.filter((row) => isSelected(row.invoice_id)).length;
  const headerState = rows.length > 0 && selectedOnPage === rows.length ? true : selectedOnPage > 0 ? "indeterminate" : false;

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1040px] border-collapse text-left">
        <caption className="sr-only">{caption}</caption>
        <colgroup>
          <col className="w-[48px]" />
          <col className="w-[156px]" />
          <col />
          <col className="w-[116px]" />
          <col className="w-[214px]" />
          <col className="w-[160px]" />
          <col className="w-[128px]" />
          <col className="w-[128px]" />
        </colgroup>
        <thead>
          <tr className="h-10 border-b border-line">
            <th scope="col" className="pl-4 pr-2">
              <Checkbox
                checked={headerState}
                disabled={rows.length === 0}
                onCheckedChange={() => onTogglePage(headerState !== true)}
                aria-label={headerState === true ? "Clear selection on this page" : "Select every bill on this page"}
              />
            </th>
            <SortHeader label="Invoice #" sortKey="invoice_number" sort={sort} onSort={onSort} />
            <SortHeader label="Vendor" sortKey="vendor" sort={sort} onSort={onSort} />
            <SortHeader label="Issue date" sortKey="invoice_date" sort={sort} onSort={onSort} />
            <SortHeader label="Approved" sortKey="approved" sort={sort} onSort={onSort} />
            <SortHeader label="Total" sortKey="total" sort={sort} onSort={onSort} align="right" />
            <SortHeader label="Confidence" sortKey="confidence" sort={sort} onSort={onSort} />
            <th scope="col" className="px-3 pr-4">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.length > 0
            ? rows.map((invoice) => <ArchiveRow key={invoice.invoice_id} invoice={invoice} {...props} />)
            : skeletonRows > 0
              ? Array.from({ length: skeletonRows }, (_, index) => (
                  <tr key={index} aria-hidden>
                    <td colSpan={COLUMNS} className="p-0">
                      <SkeletonRow index={index} className={cn("h-[52px]", index === skeletonRows - 1 && "border-b-0")} />
                    </td>
                  </tr>
                ))
              : (
                  <tr>
                    <td colSpan={COLUMNS} className="h-[360px] p-0 align-middle">
                      {empty}
                    </td>
                  </tr>
                )}
        </tbody>
      </table>
    </div>
  );
}

function ArchiveRow({ invoice, isSelected, onToggle, onOpenReport, onExport, touched, pending, reportId, now }: ApprovedBillsTableProps & { invoice: InvoiceRecord }) {
  const id = invoice.invoice_id;
  const selected = isSelected(id);
  const saving = Boolean(pending[id]);
  const arrived = (touched[id] ?? 0) > Date.now() - ARRIVAL_HIGHLIGHT_MS;
  const approval = invoice.approval;
  const label = invoice.invoice_number ?? "invoice";

  return (
    <tr
      onClick={() => onOpenReport(id)}
      aria-selected={selected}
      className={cn(
        "group h-[52px] cursor-pointer border-b border-line text-[13px] transition-colors last:border-b-0",
        selected ? "bg-accent/[0.06] hover:bg-accent/[0.09]" : "hover:bg-hover/60",
        reportId === id && "bg-hover",
        arrived && "animate-row-arrive",
      )}
    >
      <td className="pl-4 pr-2" onClick={(event) => event.stopPropagation()}>
        <Checkbox checked={selected} onCheckedChange={(checked) => onToggle(id, checked === true)} aria-label={`Select ${label}`} />
      </td>
      <td className="px-3">
        <span className="figure block truncate text-ink">{invoice.invoice_number ?? "—"}</span>
        <span className="mt-0.5 block truncate text-[11px] text-ink-3" title={invoice.document.filename}>
          {invoice.document.filename}
        </span>
      </td>
      <td className="max-w-0 px-3">
        <span className="block truncate text-ink">{invoice.vendor.name ?? "Unknown vendor"}</span>
        <span className="figure mt-0.5 block truncate text-[11px] text-ink-3">{invoice.vendor.tax_id ?? "No tax ID"}</span>
      </td>
      <td className="px-3 tabular-nums text-ink-2">{invoice.invoice_date ? formatDate(invoice.invoice_date) : "—"}</td>
      <td className="px-3">
        {approval ? (
          <Tooltip content={<span className="figure">{formatDateTime(approval.at)}</span>} align="start">
            <span className="block min-w-0" tabIndex={-1}>
              <span className="block tabular-nums text-ink">{formatDay(approval.at)}</span>
              <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] text-ink-3">
                {saving ? (
                  <>
                    <LoaderCircle className="size-3 animate-spin" aria-hidden />
                    Archiving…
                  </>
                ) : approval.method === "automatic" ? (
                  <>
                    <Bot className="size-3.5 shrink-0" aria-hidden />
                    <span className="truncate">Automatically · {relativeTime(approval.at, now)}</span>
                  </>
                ) : (
                  <>
                    <Avatar name={approval.by?.name ?? "?"} className="size-4 text-[8px]" />
                    <span className="truncate">
                      <span className="text-ink-2">{approval.by?.name ?? "A reviewer"}</span> · {relativeTime(approval.at, now)}
                    </span>
                  </>
                )}
              </span>
            </span>
          </Tooltip>
        ) : (
          <span className="text-ink-3">—</span>
        )}
      </td>
      <td className="px-3 text-right">
        <Money amount={invoice.financial_summary.total} currency={invoice.financial_summary.currency} className="text-[13px] text-ink" />
      </td>
      <td className="px-3">
        <span onClick={(event) => event.stopPropagation()}>
          <ConfidenceBadge invoice={invoice} />
        </span>
      </td>
      <td className="px-3 pr-4 text-right" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-end gap-1">
          <Button size="xs" variant="ghost" onClick={() => onOpenReport(id)} aria-label={`View audit report for ${label}`}>
            <FileText aria-hidden /> Report
          </Button>
          <RowMenu invoice={invoice} disabled={saving} onOpenReport={() => onOpenReport(id)} onExport={(format) => onExport(invoice, format)} />
        </div>
      </td>
    </tr>
  );
}

function RowMenu({
  invoice,
  disabled,
  onOpenReport,
  onExport,
}: {
  invoice: InvoiceRecord;
  disabled: boolean;
  onOpenReport: () => void;
  onExport: (format: ExportFormat) => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <DropdownMenu onOpenChange={() => setCopied(false)}>
      <DropdownMenuTrigger asChild>
        <Button size="icon-sm" variant="ghost" disabled={disabled} aria-label={`More actions for ${invoice.invoice_number ?? "invoice"}`} className="data-[state=open]:bg-hover data-[state=open]:text-ink">
          <MoreHorizontal />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuItem onSelect={onOpenReport}>
          <FileText className="text-ink-3" aria-hidden /> View audit report
        </DropdownMenuItem>
        {invoice.document.available ? (
          <DropdownMenuItem asChild>
            <a href={api.documentUrl(invoice.invoice_id)} target="_blank" rel="noopener noreferrer">
              <ExternalLink className="text-ink-3" aria-hidden /> Open original document
            </a>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Export</DropdownMenuLabel>
        <DropdownMenuItem onSelect={() => onExport("json")}>
          <Braces className="text-ink-3" aria-hidden /> Full audit record
          <span className="ml-auto font-mono text-[11px] text-ink-3">JSON</span>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onExport("csv")}>
          <FileSpreadsheet className="text-ink-3" aria-hidden /> Line items
          <span className="ml-auto font-mono text-[11px] text-ink-3">CSV</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={(event) => {
            event.preventDefault();
            navigator.clipboard?.writeText(invoice.invoice_id).then(() => setCopied(true), () => undefined);
          }}
        >
          {copied ? <Check className="text-approved" aria-hidden /> : <Copy className="text-ink-3" aria-hidden />}
          {copied ? "Copied" : "Copy invoice ID"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
