import { Archive, CalendarRange, ChevronDown, Inbox, LoaderCircle, RotateCw, SearchX, TriangleAlert } from "lucide-react";
import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Pagination } from "@/components/feed/InvoiceTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { SearchInput } from "@/components/ui/search-input";
import { Segmented } from "@/components/ui/segmented";
import { useHotkey } from "@/hooks/use-hotkey";
import { useNow } from "@/hooks/use-now";
import { navigate } from "@/hooks/use-route";
import { useSelection } from "@/hooks/use-selection";
import { exportInvoice, exportInvoices, type ExportFormat } from "@/lib/export";
import { invoiceLabel, plural } from "@/lib/format";
import type { InvoiceRecord } from "@/lib/types";
import { ARCHIVE_CAP, useAudit, useOrganization } from "@/state/audit-store";
import { ApprovedBillsTable } from "./ApprovedBillsTable";
import { ApprovedSummary } from "./ApprovedSummary";
import { filterArchive, PERIODS, sortArchive, summarize, type ArchiveSort, type ArchiveSortKey, type MethodFilter, type PeriodFilter } from "./archive";
import { AuditReportSheet } from "./AuditReportSheet";
import { BulkActionBar } from "./BulkActionBar";

const PAGE_SIZE = 25;

/** The archive of approved invoices: search, filter, inspect the audit report, export. */
export function ApprovedBillsView() {
  const { invoices, archive, loadArchive, touched, pending } = useAudit();
  const organization = useOrganization();
  const now = useNow(30_000);
  const [query, setQuery] = useState("");
  const [method, setMethod] = useState<MethodFilter>("all");
  const [period, setPeriod] = useState<PeriodFilter>("all");
  const [sort, setSort] = useState<ArchiveSort>({ key: "approved", dir: "desc" });
  const [page, setPage] = useState(1);
  const [report, setReport] = useState<{ id: string; sequence: string[] | null } | null>(null);
  const deferredQuery = useDeferredValue(query);

  const approved = useMemo(() => invoices.filter((invoice) => invoice.status === "APPROVED"), [invoices]);
  const approvedIds = useMemo(() => approved.map((invoice) => invoice.invoice_id), [approved]);
  const selection = useSelection(approvedIds);

  // The day boundary for period filters only needs to move once a minute.
  const minute = Math.floor(now / 60_000);
  const filtered = useMemo(
    () => sortArchive(filterArchive(approved, { query: deferredQuery, method, period }, minute * 60_000), sort, organization.currency_code),
    [approved, deferredQuery, method, period, minute, sort, organization.currency_code],
  );
  const methodCounts = useMemo(() => {
    const scoped = filterArchive(approved, { query: deferredQuery, method: "all", period }, minute * 60_000);
    const reviewer = scoped.filter((invoice) => invoice.approval?.method === "reviewer").length;
    return { all: scoped.length, reviewer, automatic: scoped.length - reviewer };
  }, [approved, deferredQuery, period, minute]);
  const summary = useMemo(() => summarize(filtered, organization.currency_code), [filtered, organization.currency_code]);

  useEffect(() => setPage(1), [deferredQuery, method, period, sort]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const firstIndex = (currentPage - 1) * PAGE_SIZE;
  const visible = filtered.slice(firstIndex, firstIndex + PAGE_SIZE);

  const loading = archive.status === "idle" || archive.status === "loading";
  const holdSkeleton = loading && approved.length === 0;
  const filtersActive = deferredQuery.trim() !== "" || method !== "all" || period !== "all";
  const selectedInvoices = useMemo(() => filtered.filter((invoice) => selection.has(invoice.invoice_id)), [filtered, selection]);
  // Selected bills hidden by the current filters still count, and still export.
  const selectedAll = useMemo(() => approved.filter((invoice) => selection.selected.has(invoice.invoice_id)), [approved, selection.selected]);

  useHotkey("Escape", selection.clear, { enabled: selection.selected.size > 0 });

  const onSort = (key: ArchiveSortKey) =>
    setSort((current) =>
      current.key === key ? { key, dir: current.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "vendor" || key === "invoice_number" ? "asc" : "desc" },
    );

  const clearFilters = () => {
    setQuery("");
    setMethod("all");
    setPeriod("all");
  };

  const exportOne = useCallback(
    (invoice: InvoiceRecord, format: ExportFormat) => {
      try {
        const filename = exportInvoice(invoice, format, organization);
        toast.success(`Exported ${invoiceLabel(invoice)}`, { description: filename });
      } catch (error) {
        toast.error("Export failed", { description: error instanceof Error ? error.message : "The file couldn't be generated." });
      }
    },
    [organization],
  );

  const exportList = (list: InvoiceRecord[], format: ExportFormat) => {
    try {
      const filename = exportInvoices(list, format, organization);
      toast.success(`Exported ${plural(list.length, "approved bill")}`, { description: filename });
    } catch (error) {
      toast.error("Export failed", { description: error instanceof Error ? error.message : "The file couldn't be generated." });
    }
  };

  const periodLabel = PERIODS.find((option) => option.value === period)!.label;
  const waiting = invoices.filter((invoice) => invoice.status === "NEEDS_REVIEW").length;

  return (
    <>
      <main className="mx-auto flex max-w-[1600px] animate-fade-in flex-col gap-5 px-4 pb-28 pt-6 sm:px-6">
        <PageHeader
          title="Approved Bills"
          description={
            <>
              Every invoice approved for payment, automatically or by a reviewer, with its full audit report ·{" "}
              <span className="text-ink-2">{organization.legal_name ?? organization.name}</span>
            </>
          }
          actions={
            <Button variant="secondary" disabled={filtered.length === 0} onClick={() => exportList(filtered, "csv")}>
              <Archive aria-hidden /> Export {filtersActive ? "filtered" : "all"} · CSV
            </Button>
          }
        />

        <ErrorBoundary label="The approved bills summary">
          <ApprovedSummary summary={summary} currency={organization.currency_code} scope={filtersActive ? "Filtered view" : periodLabel} loading={holdSkeleton} />
        </ErrorBoundary>

        <ErrorBoundary label="The approved bills table">
          <section aria-labelledby="archive-heading" className="glass flex min-w-0 flex-col overflow-hidden rounded-xl">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pb-3 pt-4">
              <h2 id="archive-heading" className="text-[15px] font-semibold tracking-[-0.01em]">
                Audit archive
              </h2>
              {archive.status === "loading" && !holdSkeleton ? (
                <span className="flex items-center gap-1.5 text-[12px] text-ink-3" role="status">
                  <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> Loading older approvals…
                </span>
              ) : null}
              <div className="ml-auto flex w-full flex-wrap items-center gap-2 lg:w-auto">
                <Segmented
                  label="Approval method"
                  value={method}
                  onValueChange={setMethod}
                  options={[
                    { value: "all", label: "All", count: methodCounts.all },
                    { value: "reviewer", label: "Reviewer", count: methodCounts.reviewer },
                    { value: "automatic", label: "Automatic", count: methodCounts.automatic },
                  ]}
                />
                <PeriodMenu value={period} onChange={setPeriod} label={periodLabel} />
                <SearchInput
                  id="archive-search"
                  label="Search approved bills"
                  value={query}
                  onChange={setQuery}
                  placeholder="Invoice no., vendor, approver, tax ID"
                  className="w-full sm:w-72"
                />
              </div>
            </div>

            {archive.status === "error" ? (
              <div role="alert" className="flex flex-wrap items-center gap-3 border-t border-line bg-rejected/[0.05] px-4 py-2.5 text-[12px]">
                <TriangleAlert className="size-4 shrink-0 text-rejected" aria-hidden />
                <p className="min-w-0 flex-1 text-ink-2">
                  <span className="font-medium text-ink">Couldn't load the full archive.</span> {archive.error.message} Approvals from the last 90 days are
                  shown.
                </p>
                <Button size="xs" variant="secondary" onClick={loadArchive}>
                  <RotateCw aria-hidden /> Retry
                </Button>
              </div>
            ) : null}
            {archive.truncated ? (
              <p className="border-t border-line px-4 py-2 text-[12px] text-review">
                Showing the {ARCHIVE_CAP.toLocaleString()} most recently received approved bills. Narrow with search or the period filter.
              </p>
            ) : null}

            <div className="border-t border-line">
              <ApprovedBillsTable
                rows={visible}
                skeletonRows={holdSkeleton ? 8 : 0}
                sort={sort}
                onSort={onSort}
                isSelected={selection.has}
                onToggle={selection.toggle}
                onTogglePage={(on) => selection.setMany(visible.map((invoice) => invoice.invoice_id), on)}
                onOpenReport={(id) => setReport({ id, sequence: null })}
                onExport={exportOne}
                touched={touched}
                pending={pending}
                reportId={report?.id ?? null}
                now={now}
                caption={`Approved bills for ${organization.name}, sorted by ${sort.key.replace("_", " ")} ${sort.dir === "asc" ? "ascending" : "descending"}`}
                empty={
                  filtersActive ? (
                    <EmptyState icon={SearchX} title="No approved bills match these filters" description="Search covers invoice numbers, vendors, approvers, tax IDs and file names.">
                      <Button size="sm" variant="secondary" onClick={clearFilters}>
                        Clear filters
                      </Button>
                    </EmptyState>
                  ) : (
                    <EmptyState
                      icon={Archive}
                      tone="approved"
                      title="No approved bills yet"
                      description="Invoices land here when the rule engine approves them automatically, or when a reviewer approves & archives one from the review queue."
                    >
                      <Button size="sm" variant="primary" onClick={() => navigate("review")}>
                        <Inbox aria-hidden /> {waiting ? `Review ${plural(waiting, "waiting invoice")}` : "Go to the review queue"}
                      </Button>
                    </EmptyState>
                  )
                }
              />
            </div>

            <footer className="flex h-11 flex-wrap items-center justify-between gap-3 border-t border-line px-4 text-[12px] text-ink-3">
              <span>
                {selection.selected.size > 0 ? (
                  <>
                    <span className="figure text-ink-2">{selection.selected.size}</span> selected
                    {selectedAll.length > selectedInvoices.length ? ` · ${selectedAll.length - selectedInvoices.length} hidden by filters` : ""}
                  </>
                ) : (
                  "Click a row for its audit report"
                )}
              </span>
              <div className="flex items-center gap-3">
                <span className="figure" aria-live="polite">
                  {filtered.length === 0 ? "0" : `${firstIndex + 1}–${Math.min(firstIndex + PAGE_SIZE, filtered.length)}`} of {filtered.length.toLocaleString()}
                </span>
                <Pagination page={currentPage} pageCount={pageCount} onPage={setPage} />
              </div>
            </footer>
          </section>
        </ErrorBoundary>
      </main>

      {selection.selected.size > 0 ? (
        <BulkActionBar
          selected={selectedAll}
          matching={filtered.length}
          onSelectAllMatching={() => selection.setMany(filtered.map((invoice) => invoice.invoice_id), true)}
          onClear={selection.clear}
          onExport={(format) => exportList(selectedAll, format)}
          onReview={() => {
            const ordered = filtered.filter((invoice) => selection.has(invoice.invoice_id)).map((invoice) => invoice.invoice_id);
            const sequence = ordered.length ? ordered : selectedAll.map((invoice) => invoice.invoice_id);
            if (sequence[0]) setReport({ id: sequence[0], sequence });
          }}
        />
      ) : null}

      <AuditReportSheet
        invoiceId={report?.id ?? null}
        sequence={report?.sequence ?? null}
        onNavigate={(id) => setReport((current) => ({ id, sequence: current?.sequence ?? null }))}
        onClose={() => setReport(null)}
        onExport={exportOne}
      />
    </>
  );
}

function PeriodMenu({ value, onChange, label }: { value: PeriodFilter; onChange: (period: PeriodFilter) => void; label: string }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" className="data-[state=open]:border-line-strong" aria-label={`Approved within: ${label}`}>
          <CalendarRange className="text-ink-3" aria-hidden />
          {label}
          <ChevronDown className="-mr-0.5 text-ink-3" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">
        <DropdownMenuLabel>Approved within</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={value} onValueChange={(next) => onChange(next as PeriodFilter)}>
          {PERIODS.map((option) => (
            <DropdownMenuRadioItem key={option.value} value={option.value}>
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
