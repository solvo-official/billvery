import { RotateCw, SearchX, ShieldAlert, ShieldCheck, ShieldHalf, Store, TriangleAlert } from "lucide-react";
import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Pagination, SortHeader } from "@/components/feed/InvoiceTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { SkeletonRow } from "@/components/shell/DashboardSkeleton";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { SearchInput } from "@/components/ui/search-input";
import { Segmented } from "@/components/ui/segmented";
import { Tooltip } from "@/components/ui/tooltip";
import { useNow } from "@/hooks/use-now";
import { navigate } from "@/hooks/use-route";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime } from "@/lib/dates";
import type { VendorRiskTier, VendorScorecard } from "@/lib/types";
import { useAudit, useOrganization } from "@/state/audit-store";
import { VENDOR_TIER_RANK, VendorRiskBadge } from "./VendorRiskBadge";

type TierFilter = "ALL" | VendorRiskTier;
type SortKey = "risk" | "vendor" | "invoices" | "flag_rate" | "duplicates" | "rejected" | "last_invoice";
interface Sort {
  key: SortKey;
  dir: "asc" | "desc";
}

const PAGE_SIZE = 25;
const COLUMNS = 7;

function sortValue(card: VendorScorecard, key: SortKey): number | string {
  switch (key) {
    case "risk":
      // Riskiest first when descending; the flag rate breaks ties within a tier.
      return (2 - VENDOR_TIER_RANK[card.risk.tier]) * 1000 + card.flag_rate_percent;
    case "vendor":
      return card.name.toLowerCase();
    case "invoices":
      return card.total_invoices;
    case "flag_rate":
      return card.flag_rate_percent;
    case "duplicates":
      return card.duplicate_invoices;
    case "rejected":
      return card.rejected_invoices;
    case "last_invoice":
      return card.last_invoice_at;
  }
}

/** Vendor risk scorecards: each vendor's audit history over the whole ledger, riskiest first. */
export function VendorsView() {
  const { vendors, invoices, reloadVendors } = useAudit();
  const organization = useOrganization();
  const now = useNow(60_000);
  const [query, setQuery] = useState("");
  const [tier, setTier] = useState<TierFilter>("ALL");
  const [sort, setSort] = useState<Sort>({ key: "risk", dir: "desc" });
  const [page, setPage] = useState(1);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());

  const counts = useMemo(() => {
    const result: Record<TierFilter, number> = { ALL: vendors.list.length, HIGH: 0, MEDIUM: 0, LOW: 0 };
    for (const card of vendors.list) result[card.risk.tier] += 1;
    return result;
  }, [vendors.list]);

  const rows = useMemo(() => {
    const filtered = vendors.list.filter(
      (card) =>
        (tier === "ALL" || card.risk.tier === tier) &&
        (!deferredQuery || card.name.toLowerCase().includes(deferredQuery) || (card.tax_id ?? "").toLowerCase().includes(deferredQuery)),
    );
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const va = sortValue(a, sort.key);
      const vb = sortValue(b, sort.key);
      const order = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return sign * order || a.name.localeCompare(b.name);
    });
  }, [vendors.list, tier, deferredQuery, sort]);

  useEffect(() => setPage(1), [tier, deferredQuery, sort]);
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const firstIndex = (currentPage - 1) * PAGE_SIZE;
  const visible = rows.slice(firstIndex, firstIndex + PAGE_SIZE);
  const loading = vendors.status === "idle";

  const onSort = (key: SortKey) =>
    setSort((current) => (current.key === key ? { key, dir: current.dir === "asc" ? "desc" : "asc" } : { key, dir: key === "vendor" ? "asc" : "desc" }));

  return (
    <main className="mx-auto flex max-w-[1600px] animate-fade-in flex-col gap-5 px-4 pb-10 pt-6 sm:px-6">
      <PageHeader
        title="Vendors"
        description={
          <>
            Risk scorecards from every invoice each vendor has sent · <span className="text-ink-2">{organization.legal_name ?? organization.name}</span>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <TierTile tier="HIGH" count={counts.HIGH} loading={loading} onClick={() => setTier("HIGH")} active={tier === "HIGH"} />
        <TierTile tier="MEDIUM" count={counts.MEDIUM} loading={loading} onClick={() => setTier("MEDIUM")} active={tier === "MEDIUM"} />
        <TierTile tier="LOW" count={counts.LOW} loading={loading} onClick={() => setTier("LOW")} active={tier === "LOW"} />
      </div>

      <ErrorBoundary label="The vendor table">
        <section aria-labelledby="vendors-heading" className="glass flex min-w-0 flex-col overflow-hidden rounded-xl">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pb-3 pt-4">
            <h2 id="vendors-heading" className="text-[15px] font-semibold tracking-[-0.01em]">
              Vendor scorecards
            </h2>
            <p className="hidden text-[12px] text-ink-3 md:block">Updates as invoices arrive and reviews are committed</p>
            <div className="ml-auto flex w-full flex-wrap items-center gap-2 lg:w-auto">
              <Segmented
                label="Risk tier"
                value={tier}
                onValueChange={setTier}
                options={[
                  { value: "ALL", label: "All", count: counts.ALL },
                  { value: "HIGH", label: "High", count: counts.HIGH },
                  { value: "MEDIUM", label: "Medium", count: counts.MEDIUM },
                  { value: "LOW", label: "Low", count: counts.LOW },
                ]}
              />
              <SearchInput id="vendor-search" label="Search vendors" value={query} onChange={setQuery} placeholder="Vendor name or tax ID" className="w-full sm:w-64" />
            </div>
          </div>

          {vendors.status === "error" && vendors.error ? (
            <div role="alert" className="flex flex-wrap items-center gap-3 border-t border-line bg-rejected/[0.05] px-4 py-2.5 text-[12px]">
              <TriangleAlert className="size-4 shrink-0 text-rejected" aria-hidden />
              <p className="min-w-0 flex-1 text-ink-2">
                <span className="font-medium text-ink">Couldn't load the scorecards.</span> {vendors.error.message}
              </p>
              <Button size="xs" variant="secondary" onClick={reloadVendors}>
                <RotateCw aria-hidden /> Retry
              </Button>
            </div>
          ) : null}

          <div className="overflow-x-auto border-t border-line">
            <table className="w-full min-w-[1000px] border-collapse text-left">
              <caption className="sr-only">
                Vendors for {organization.name}, sorted by {sort.key.replace("_", " ")} {sort.dir === "asc" ? "ascending" : "descending"}
              </caption>
              <colgroup>
                <col />
                <col className="w-[260px]" />
                <col className="w-[112px]" />
                <col className="w-[176px]" />
                <col className="w-[128px]" />
                <col className="w-[100px]" />
                <col className="w-[136px]" />
              </colgroup>
              <thead>
                <tr className="h-10 border-b border-line">
                  <SortHeader label="Vendor" sortKey="vendor" sort={sort} onSort={onSort} />
                  <SortHeader label="Risk" sortKey="risk" sort={sort} onSort={onSort} />
                  <SortHeader label="Invoices" sortKey="invoices" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Held for review" sortKey="flag_rate" sort={sort} onSort={onSort} />
                  <SortHeader label="Duplicates" sortKey="duplicates" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Rejected" sortKey="rejected" sort={sort} onSort={onSort} align="right" />
                  <SortHeader label="Last invoice" sortKey="last_invoice" sort={sort} onSort={onSort} />
                </tr>
              </thead>
              <tbody>
                {loading
                  ? Array.from({ length: 6 }, (_, index) => (
                      <tr key={index} aria-hidden>
                        <td colSpan={COLUMNS} className="p-0">
                          <SkeletonRow index={index} className="h-[56px]" />
                        </td>
                      </tr>
                    ))
                  : visible.map((card) => <VendorRow key={card.vendor_id} card={card} now={now} />)}
                {!loading && visible.length === 0 ? (
                  <tr>
                    <td colSpan={COLUMNS} className="h-[320px] p-0 align-middle">
                      {vendors.list.length === 0 ? (
                        <EmptyState
                          icon={Store}
                          title="No vendors yet"
                          description={
                            invoices.length
                              ? "Vendors appear once an invoice's supplier name could be read."
                              : "Each supplier gets a scorecard with its first invoice. Upload one to start."
                          }
                        >
                          <Button size="sm" variant="secondary" onClick={() => navigate("review")}>
                            Go to the review queue
                          </Button>
                        </EmptyState>
                      ) : (
                        <EmptyState icon={SearchX} title="No vendors match" description="Search covers vendor names and tax IDs.">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => {
                              setQuery("");
                              setTier("ALL");
                            }}
                          >
                            Clear filters
                          </Button>
                        </EmptyState>
                      )}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <footer className="flex h-11 flex-wrap items-center justify-between gap-3 border-t border-line px-4 text-[12px] text-ink-3">
            <span>Hover a risk badge for the history behind it</span>
            <div className="flex items-center gap-3">
              <span className="figure" aria-live="polite">
                {rows.length === 0 ? "0" : `${firstIndex + 1}–${Math.min(firstIndex + PAGE_SIZE, rows.length)}`} of {rows.length.toLocaleString()}
              </span>
              <Pagination page={currentPage} pageCount={pageCount} onPage={setPage} />
            </div>
          </footer>
        </section>
      </ErrorBoundary>
    </main>
  );
}

const TILE: Record<VendorRiskTier, { title: string; hint: string; icon: typeof ShieldAlert; tone: string }> = {
  HIGH: { title: "High risk", hint: "Rejected invoices, confirmed duplicates or frequent review holds", icon: ShieldAlert, tone: "text-rejected bg-rejected/10 ring-rejected/20" },
  MEDIUM: { title: "Medium risk", hint: "Noticeable review rate or a possible duplicate", icon: ShieldHalf, tone: "text-review bg-review/10 ring-review/25" },
  LOW: { title: "Low risk", hint: "Clean history", icon: ShieldCheck, tone: "text-approved bg-approved/10 ring-approved/20" },
};

function TierTile({ tier, count, loading, onClick, active }: { tier: VendorRiskTier; count: number; loading: boolean; onClick: () => void; active: boolean }) {
  const tile = TILE[tier];
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "glass flex cursor-pointer items-center gap-3.5 rounded-xl px-4 py-3.5 text-left transition-[box-shadow,border-color]",
        active && "shadow-[0_0_0_2px_color-mix(in_oklab,var(--accent)_45%,transparent)]",
      )}
    >
      <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset", tile.tone)} aria-hidden>
        <tile.icon className="size-[18px]" strokeWidth={1.75} />
      </span>
      <span className="min-w-0">
        <span className="block text-[12px] text-ink-3">{tile.title}</span>
        <span className="figure block text-[22px] font-semibold leading-7 text-ink">{loading ? "—" : count.toLocaleString()}</span>
        <span className="block truncate text-[11px] text-ink-3">{tile.hint}</span>
      </span>
    </button>
  );
}

function VendorRow({ card, now }: { card: VendorScorecard; now: number }) {
  return (
    <tr className="h-[56px] border-b border-line text-[13px] last:border-b-0">
      <td className="max-w-0 px-3 pl-4">
        <span className="block truncate text-ink">{card.name}</span>
        <span className="figure mt-0.5 block truncate text-[11px] text-ink-3">{card.tax_id ?? "No tax ID"}</span>
      </td>
      <td className="max-w-0 px-3">
        <VendorRiskBadge card={card} />
        <span className="mt-0.5 block truncate text-[11px] text-ink-3" title={card.risk.reasons.join("; ")}>
          {card.risk.reasons[0]}
        </span>
      </td>
      <td className="figure px-3 text-right text-ink">
        {card.total_invoices.toLocaleString()}
        {card.in_review ? <span className="block whitespace-nowrap font-sans text-[11px] text-review">{card.in_review} in review</span> : null}
      </td>
      <td className="px-3">
        <span className="flex items-center gap-2">
          <span className="figure w-11 text-right text-[12px] text-ink">{card.flag_rate_percent}%</span>
          <span className="relative h-1 w-16 overflow-hidden rounded-full bg-ink-3/15" aria-hidden>
            <span
              className={cn("absolute inset-y-0 left-0 rounded-full", card.flag_rate_percent >= 50 ? "bg-sev-high" : card.flag_rate_percent >= 20 ? "bg-sev-medium" : "bg-sev-low")}
              style={{ width: `${Math.max(card.flag_rate_percent, 3)}%` }}
            />
          </span>
        </span>
        <span className="mt-0.5 block text-[11px] text-ink-3">
          {card.held_for_review} of {card.total_invoices}
        </span>
      </td>
      <td className="figure px-3 text-right">
        <span className={card.duplicate_invoices ? "text-ink" : "text-ink-3"}>{card.duplicate_invoices}</span>
        {card.confirmed_duplicates ? <span className="block text-[11px] text-rejected">{card.confirmed_duplicates} confirmed</span> : null}
      </td>
      <td className={cn("figure px-3 text-right", card.rejected_invoices ? "text-rejected" : "text-ink-3")}>{card.rejected_invoices}</td>
      <td className="px-3 text-ink-2">
        <Tooltip content={<span className="figure">{formatDateTime(card.last_invoice_at)}</span>}>
          <span tabIndex={0} className="cursor-default">
            {relativeTime(card.last_invoice_at, now)}
          </span>
        </Tooltip>
      </td>
    </tr>
  );
}
