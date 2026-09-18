import {
  Archive,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ChevronsUpDown,
  FileDown,
  Inbox,
  Plug,
  SearchX,
  TriangleAlert,
  Upload,
  type LucideIcon,
} from "lucide-react";
import { Tabs } from "radix-ui";
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Money, RiskMeter, StatusBadge } from "@/components/audit/status";
import { ExportBatchDialog } from "@/components/export/ExportBatchDialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { SearchInput } from "@/components/ui/search-input";
import { Tooltip } from "@/components/ui/tooltip";
import { VendorRiskBadge } from "@/components/vendors/VendorRiskBadge";
import { useNow } from "@/hooks/use-now";
import { navigate } from "@/hooks/use-route";
import { useSelection } from "@/hooks/use-selection";
import { cn } from "@/lib/cn";
import { formatDate, relativeTime } from "@/lib/dates";
import { convert } from "@/lib/money";
import { riskScore } from "@/lib/rules";
import type { InvoiceRecord, InvoiceStatus } from "@/lib/types";
import { useAudit, useOrganization } from "@/state/audit-store";

type StatusFilter = "ALL" | Exclude<InvoiceStatus, "APPROVED">;
type SortKey = "received" | "invoice_number" | "vendor" | "invoice_date" | "total" | "risk";
interface Sort {
  key: SortKey;
  dir: "asc" | "desc";
}
interface Row {
  invoice: InvoiceRecord;
  risk: number | null;
  total: number | null;
}

/** Approved invoices live in the Approved Bills archive; "All" still shows everything loaded. */
const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "NEEDS_REVIEW", label: "Needs review" },
  { value: "PROCESSING", label: "Processing" },
  { value: "REJECTED", label: "Rejected" },
  { value: "ALL", label: "All activity" },
];

const SOURCE: Record<InvoiceRecord["source"], { icon: LucideIcon; label: string }> = {
  api: { icon: Plug, label: "API" },
  upload: { icon: Upload, label: "Upload" },
};

const PAGE_SIZES = [15, 30, 50];
const ARRIVAL_HIGHLIGHT_MS = 3_000;

/** Nulls sort last in either direction. */
function compareNullable(a: number | string | null, b: number | string | null): number {
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
  return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true });
}

function sortRows(rows: Row[], { key, dir }: Sort): Row[] {
  const sign = dir === "asc" ? 1 : -1;
  const value = (row: Row): number | string | null => {
    switch (key) {
      case "received":
        return row.invoice.created_at;
      case "invoice_number":
        return row.invoice.invoice_number;
      case "vendor":
        return row.invoice.vendor.name;
      case "invoice_date":
        return row.invoice.invoice_date;
      case "total":
        return row.total;
      case "risk":
        return row.risk;
    }
  };
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va === null || vb === null) return compareNullable(va, vb);
    return sign * compareNullable(va, vb) || b.invoice.created_at.localeCompare(a.invoice.created_at);
  });
}

function matches(invoice: InvoiceRecord, query: string): boolean {
  return [invoice.invoice_number, invoice.vendor.name, invoice.vendor.tax_id, invoice.document.filename, invoice.invoice_id]
    .filter(Boolean)
    .some((field) => field!.toLowerCase().includes(query));
}

/** Opens the upload picker in the ingestion panel. */
const browseForUpload = () => document.getElementById("document-upload")?.click();

interface InvoiceTableProps {
  selectedId: string | null;
  onOpen: (invoiceId: string) => void;
  onApprove: (invoiceId: string) => void;
}

export function InvoiceTable({ selectedId, onOpen, onApprove }: InvoiceTableProps) {
  const { invoices, touched, live, setLive, syncError, truncated, reviewer, reviewBlocker } = useAudit();
  const organization = useOrganization();
  const now = useNow(30_000);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("NEEDS_REVIEW");
  const [sort, setSort] = useState<Sort>({ key: "received", dir: "desc" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZES[0]!);
  const [exporting, setExporting] = useState(false);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  // Invoices still being extracted can't be exported, so they can't be ticked either.
  const exportableIds = useMemo(() => invoices.filter((invoice) => invoice.status !== "PROCESSING").map((invoice) => invoice.invoice_id), [invoices]);
  const selection = useSelection(exportableIds);

  useEffect(() => setPage(1), [status, deferredQuery, sort, pageSize]);

  const counts = useMemo(() => {
    const result: Record<StatusFilter, number> = { ALL: invoices.length, NEEDS_REVIEW: 0, REJECTED: 0, PROCESSING: 0 };
    for (const invoice of invoices) if (invoice.status !== "APPROVED") result[invoice.status] += 1;
    return result;
  }, [invoices]);

  const rows = useMemo(() => {
    const filtered = invoices.filter(
      (invoice) => (status === "ALL" || invoice.status === status) && (!deferredQuery || matches(invoice, deferredQuery)),
    );
    const currency = organization.currency_code;
    return sortRows(
      filtered.map((invoice) => ({
        invoice,
        risk: riskScore(invoice),
        total: invoice.financial_summary.total === null ? null : convert(invoice.financial_summary.total, invoice.financial_summary.currency, currency),
      })),
      sort,
    );
  }, [invoices, status, deferredQuery, sort, organization.currency_code]);

  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const firstIndex = (currentPage - 1) * pageSize;
  const visible = rows.slice(firstIndex, firstIndex + pageSize);
  const tickable = visible.filter((row) => row.invoice.status !== "PROCESSING").map((row) => row.invoice.invoice_id);
  const tickedOnPage = tickable.filter((id) => selection.has(id)).length;
  const pageState = tickable.length > 0 && tickedOnPage === tickable.length ? true : tickedOnPage > 0 ? "indeterminate" : false;
  const selectedIds = useMemo(() => [...selection.selected], [selection.selected]);

  const onSort = (key: SortKey) =>
    setSort((current) =>
      current.key === key
        ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "vendor" || key === "invoice_number" ? "asc" : "desc" },
    );

  const clearFilters = () => {
    setQuery("");
    setStatus("ALL");
  };

  return (
    <section aria-labelledby="feed-heading" className="glass flex min-w-0 flex-col overflow-hidden rounded-xl">
      <Tabs.Root value={status} onValueChange={(value) => setStatus(value as StatusFilter)} className="flex min-w-0 flex-col">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pb-3 pt-4">
          <h2 id="feed-heading" className="text-[15px] font-semibold tracking-[-0.01em]">
            Audit feed
          </h2>
          <button
            type="button"
            onClick={() => setLive(!live)}
            aria-pressed={live}
            className={cn(
              "inline-flex h-6 items-center gap-1.5 rounded-full px-2 text-[11px] font-medium ring-1 ring-inset transition-colors",
              live ? "bg-accent/10 text-accent ring-accent/25" : "text-ink-3 ring-line hover:text-ink-2",
            )}
          >
            <span className={cn("size-1.5 rounded-full", live ? "animate-beacon bg-accent text-accent" : "bg-ink-3")} aria-hidden />
            {live ? "Live" : "Paused"}
          </button>
          <p className={cn("hidden items-center gap-1.5 text-[12px] md:flex", syncError ? "text-review" : "text-ink-3")} aria-live="polite">
            {syncError ? (
              <>
                <TriangleAlert className="size-3.5" aria-hidden />
                Can't reach the audit API; retrying
              </>
            ) : live ? (
              "Syncs new and changed invoices every 10 s"
            ) : (
              "Updates are paused"
            )}
          </p>
          <div className="ml-auto flex w-full items-center gap-2 sm:w-auto">
            <Button variant="secondary" onClick={() => setExporting(true)} aria-label={`Export batch${selection.selected.size ? `, ${selection.selected.size} selected` : ""}`}>
              <FileDown aria-hidden /> Export batch
              {selection.selected.size ? (
                <span className="figure -mr-1 rounded-full bg-accent px-1.5 text-[11px] leading-[18px] text-on-accent">{selection.selected.size}</span>
              ) : null}
            </Button>
            <SearchInput
              id="feed-search"
              label="Search invoices"
              value={query}
              onChange={setQuery}
              placeholder="Invoice no., vendor, tax ID, file"
              className="min-w-0 flex-1 sm:w-72 sm:flex-none"
            />
          </div>
        </div>

        <Tabs.List aria-label="Filter by status" className="flex gap-1 overflow-x-auto overflow-y-hidden px-3 shadow-[inset_0_-1px_0_var(--color-line)]">
          {FILTERS.map((filter) => (
            <Tabs.Trigger
              key={filter.value}
              value={filter.value}
              className="group relative inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 px-2 text-[12.5px] font-medium text-ink-3 transition-colors hover:text-ink-2 data-[state=active]:text-ink"
            >
              {filter.label}
              <span
                className={cn(
                  "figure min-w-5 rounded-full px-1.5 text-center text-[11px] leading-[18px]",
                  filter.value === "NEEDS_REVIEW" && counts.NEEDS_REVIEW > 0 ? "bg-review/15 text-review" : "bg-ink-3/10 text-ink-3",
                )}
              >
                {counts[filter.value]}
              </span>
              <span className="absolute inset-x-1 -bottom-px h-[2px] scale-x-50 rounded-full bg-ink opacity-0 transition-[opacity,transform] duration-200 group-data-[state=active]:scale-x-100 group-data-[state=active]:opacity-100" aria-hidden />
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        <Tabs.Content value={status} className="min-w-0 outline-none" tabIndex={-1}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] border-collapse text-left">
              <caption className="sr-only">
                Invoices for {organization.name}, sorted by {sort.key.replace("_", " ")} {sort.dir === "asc" ? "ascending" : "descending"}
              </caption>
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[156px]" />
                <col />
                <col className="w-[112px]" />
                <col className="w-[160px]" />
                <col className="w-[100px]" />
                <col className="w-[148px]" />
                <col className="w-[188px]" />
              </colgroup>
              <thead>
                <tr className="h-9 border-b border-line">
                  <th scope="col" className="pl-4 pr-1">
                    <Checkbox
                      checked={pageState}
                      disabled={tickable.length === 0}
                      onCheckedChange={() => selection.setMany(tickable, pageState !== true)}
                      aria-label={pageState === true ? "Clear selection on this page" : "Select every invoice on this page"}
                    />
                  </th>
                  <SortHeader label="Invoice #" sortKey="invoice_number" sort={sort} onSort={onSort} />
                  <SortHeader label="Vendor" sortKey="vendor" sort={sort} onSort={onSort} />
                  <SortHeader label="Issue date" sortKey="invoice_date" sort={sort} onSort={onSort} />
                  <SortHeader label="Total" sortKey="total" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Risk" sortKey="risk" sort={sort} onSort={onSort} />
                  <th scope="col" className="eyebrow px-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-3">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((row) => (
                  <InvoiceRow
                    key={row.invoice.invoice_id}
                    row={row}
                    now={now}
                    selected={row.invoice.invoice_id === selectedId}
                    checked={selection.has(row.invoice.invoice_id)}
                    onCheck={(on) => selection.toggle(row.invoice.invoice_id, on)}
                    arrived={(touched[row.invoice.invoice_id] ?? 0) > Date.now() - ARRIVAL_HIGHLIGHT_MS}
                    canApprove={reviewer !== null}
                    approveHint={reviewBlocker}
                    onOpen={onOpen}
                    onApprove={onApprove}
                  />
                ))}
                {visible.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="h-[340px] p-0 align-middle">
                      {deferredQuery ? (
                        <EmptyState icon={SearchX} title={`No invoices match “${query.trim()}”`} description="Search covers invoice numbers, vendors, tax IDs and file names.">
                          <Button size="sm" variant="secondary" onClick={() => setQuery("")}>
                            Clear search
                          </Button>
                          {status !== "ALL" ? (
                            <Button size="sm" variant="ghost" onClick={clearFilters}>
                              Search all activity
                            </Button>
                          ) : null}
                        </EmptyState>
                      ) : status === "NEEDS_REVIEW" ? (
                        <EmptyState
                          icon={CheckCheck}
                          tone="approved"
                          title="You're all caught up"
                          description="Nothing is waiting for review. Invoices that trip a HIGH or CRITICAL rule, or that Gemini reads with low confidence, land here."
                        >
                          <Button size="sm" variant="primary" onClick={browseForUpload}>
                            <Upload aria-hidden /> Upload invoices
                          </Button>
                          <Button size="sm" variant="secondary" onClick={() => navigate("approved")}>
                            <Archive aria-hidden /> Open Approved Bills
                          </Button>
                        </EmptyState>
                      ) : status === "ALL" ? (
                        <EmptyState icon={Inbox} title="No invoices yet" description="Upload a PDF or image, or send invoices through the API, and every audit rule runs against your ledger.">
                          <Button size="sm" variant="primary" onClick={browseForUpload}>
                            <Upload aria-hidden /> Upload your first invoice
                          </Button>
                        </EmptyState>
                      ) : (
                        <EmptyState
                          icon={Inbox}
                          title={`No ${FILTERS.find((f) => f.value === status)?.label.toLowerCase()} invoices`}
                          description={status === "REJECTED" ? "Invoices are rejected when a reviewer confirms a HIGH or CRITICAL finding." : "Uploads appear here while Gemini extracts them."}
                        >
                          <Button size="sm" variant="secondary" onClick={() => setStatus("NEEDS_REVIEW")}>
                            Back to the review queue
                          </Button>
                        </EmptyState>
                      )}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </Tabs.Content>
      </Tabs.Root>

      {truncated ? (
        <p className="border-t border-line px-4 py-2 text-[12px] text-review">
          Showing the most recent 5,000 invoices of the last 90 days. Search narrows the feed; older history isn't loaded.
        </p>
      ) : null}
      <footer className="flex min-h-11 flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-1.5 text-[12px] text-ink-3">
        <div className="flex items-center gap-2">
          {selection.selected.size > 0 ? (
            <span className="mr-2 flex items-center gap-2 border-r border-line pr-3">
              <span>
                <span className="figure text-ink-2">{selection.selected.size}</span> selected
              </span>
              <button type="button" onClick={selection.clear} className="cursor-pointer font-medium text-accent hover:underline">
                Clear
              </button>
            </span>
          ) : null}
          <label htmlFor="page-size">Rows per page</label>
          <select
            id="page-size"
            value={pageSize}
            onChange={(event) => setPageSize(Number(event.target.value))}
            className="figure h-7 cursor-pointer rounded-md border border-line bg-surface-solid px-1.5 text-[12px] text-ink focus:border-accent/60 focus:outline-none"
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-3">
          <span className="figure" aria-live="polite">
            {rows.length === 0 ? "0" : `${firstIndex + 1}–${Math.min(firstIndex + pageSize, rows.length)}`} of {rows.length}
          </span>
          <Pagination page={currentPage} pageCount={pageCount} onPage={setPage} />
        </div>
      </footer>
      <ExportBatchDialog open={exporting} onClose={() => setExporting(false)} selectedIds={selectedIds} />
    </section>
  );
}

export function SortHeader<K extends string>({
  label,
  sortKey,
  sort,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: K;
  sort: { key: K; dir: "asc" | "desc" };
  onSort: (key: K) => void;
  align?: "left" | "right";
}) {
  const active = sort.key === sortKey;
  const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th scope="col" aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"} className={cn("px-3", align === "right" && "text-right")}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cn(
          "eyebrow inline-flex cursor-pointer items-center gap-1 transition-colors hover:text-ink-2",
          active && "text-ink-2",
          align === "right" && "flex-row-reverse",
        )}
      >
        {label}
        <Icon className={cn("size-3", !active && "opacity-40")} aria-hidden />
      </button>
    </th>
  );
}

function InvoiceRow({
  row,
  now,
  selected,
  checked,
  onCheck,
  arrived,
  canApprove,
  approveHint,
  onOpen,
  onApprove,
}: {
  row: Row;
  now: number;
  /** open in the inspection drawer */
  selected: boolean;
  /** ticked for a batch export */
  checked: boolean;
  onCheck: (on: boolean) => void;
  arrived: boolean;
  canApprove: boolean;
  approveHint: string | null;
  onOpen: (invoiceId: string) => void;
  onApprove: (invoiceId: string) => void;
}) {
  const { invoice, risk } = row;
  const processing = invoice.status === "PROCESSING";
  const review = invoice.status === "NEEDS_REVIEW";
  const openFlags = invoice.anomalies.filter((a) => a.status === "OPEN").length;
  const source = SOURCE[invoice.source];
  const SourceIcon = source.icon;
  const open = () => !processing && onOpen(invoice.invoice_id);

  return (
    <tr
      onClick={open}
      className={cn(
        "group h-12 border-b border-line text-[13px] transition-colors last:border-b-0",
        processing ? "cursor-default" : "cursor-pointer hover:bg-hover/60",
        checked && "bg-accent/[0.05] hover:bg-accent/[0.08]",
        selected && "bg-hover",
        arrived && "animate-row-arrive",
      )}
    >
      <td className="pl-4 pr-1" onClick={(event) => event.stopPropagation()}>
        <Checkbox
          checked={checked}
          disabled={processing}
          onCheckedChange={(value) => onCheck(value === true)}
          aria-label={`Select ${invoice.invoice_number ?? invoice.document.filename} for export`}
        />
      </td>
      <td className="px-3 align-middle">
        {processing ? <span className="skeleton-text block h-3.5 w-24" aria-label="Invoice number pending" /> : <span className="figure block truncate text-ink">{invoice.invoice_number ?? "—"}</span>}
        <span className="mt-0.5 flex items-center gap-1 text-[11px] text-ink-3">
          <SourceIcon className="size-3" aria-hidden />
          <span className="truncate">
            {source.label} · {relativeTime(invoice.created_at, now)}
          </span>
        </span>
      </td>
      <td className="max-w-0 px-3">
        {processing ? (
          <>
            <span className="block truncate text-ink-2">{invoice.document.filename}</span>
            <span className="mt-0.5 block text-[11px] text-ink-3">Extracting fields…</span>
          </>
        ) : (
          <>
            <span className="block truncate text-ink">{invoice.vendor.name ?? "Unknown vendor"}</span>
            <span className="mt-0.5 flex min-w-0 items-center gap-1.5 overflow-hidden" onClick={(event) => event.stopPropagation()}>
              <VendorRiskBadge vendorId={invoice.vendor_on_file?.id} size="sm" />
              {invoice.vendor_on_file?.first_seen ? (
                <Tooltip content="First invoice from this vendor: the vendor record was created by it.">
                  <span className="shrink-0 rounded-full bg-accent/10 px-1.5 text-[10.5px] font-medium leading-4 text-accent ring-1 ring-inset ring-accent/20">New</span>
                </Tooltip>
              ) : null}
            </span>
          </>
        )}
      </td>
      <td className="px-3 tabular-nums text-ink-2">
        {processing ? <span className="skeleton-text block h-3.5 w-20" /> : invoice.invoice_date ? formatDate(invoice.invoice_date) : "—"}
      </td>
      <td className="px-3 text-right">
        {processing ? (
          <span className="skeleton-text ml-auto block h-3.5 w-24" />
        ) : (
          <Money amount={invoice.financial_summary.total} currency={invoice.financial_summary.currency} className="text-[13px] text-ink" />
        )}
      </td>
      <td className="px-3" onClick={(event) => event.stopPropagation()}>
        <RiskMeter score={risk} />
      </td>
      <td className="px-3">
        <StatusBadge status={invoice.status} openFlags={openFlags} />
      </td>
      <td className="px-3 text-right" onClick={(event) => event.stopPropagation()}>
        {processing ? (
          <span className="text-[12px] text-ink-3">Auditing…</span>
        ) : review ? (
          <div className="flex items-center justify-end gap-1.5">
            <Button size="xs" variant="ghost" onClick={open} aria-label={`Review ${invoice.invoice_number ?? "invoice"}`}>
              Review
            </Button>
            <Tooltip content={canApprove ? "Dismiss the open flags and move to Approved Bills" : (approveHint ?? "Approving isn't available.")}>
              <span>
                <Button
                  size="xs"
                  variant="approve"
                  disabled={!canApprove}
                  onClick={() => onApprove(invoice.invoice_id)}
                  aria-label={`Approve and archive ${invoice.invoice_number ?? "invoice"}`}
                >
                  <Archive aria-hidden /> Approve
                </Button>
              </span>
            </Tooltip>
          </div>
        ) : (
          <Button size="xs" variant="ghost" onClick={open} aria-label={`View ${invoice.invoice_number ?? "invoice"}`}>
            View <ArrowRight aria-hidden />
          </Button>
        )}
      </td>
    </tr>
  );
}

export function Pagination({ page, pageCount, onPage }: { page: number; pageCount: number; onPage: (page: number) => void }) {
  return (
    <nav aria-label="Pagination" className="flex items-center gap-0.5">
      <PageButton label="First page" icon={ChevronsLeft} disabled={page === 1} onClick={() => onPage(1)} />
      <PageButton label="Previous page" icon={ChevronLeft} disabled={page === 1} onClick={() => onPage(page - 1)} />
      <span className="figure min-w-14 px-1.5 text-center text-ink-2">
        {page} / {pageCount}
      </span>
      <PageButton label="Next page" icon={ChevronRight} disabled={page === pageCount} onClick={() => onPage(page + 1)} />
      <PageButton label="Last page" icon={ChevronsRight} disabled={page === pageCount} onClick={() => onPage(pageCount)} />
    </nav>
  );
}

function PageButton({ label, icon: Icon, disabled, onClick }: { label: string; icon: LucideIcon; disabled: boolean; onClick: () => void }) {
  return (
    <Button size="icon-sm" variant="ghost" aria-label={label} disabled={disabled} onClick={onClick}>
      <Icon />
    </Button>
  );
}
