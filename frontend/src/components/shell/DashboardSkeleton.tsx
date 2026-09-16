import { LoadingLabel, Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { PageHeaderSkeleton } from "./PageHeader";

/** The review workspace's shape while the ledger loads: same grid, same heights, no jump on arrival. */
export function DashboardSkeleton() {
  return (
    <main className="mx-auto flex max-w-[1600px] flex-col gap-5 px-4 pb-10 pt-6 sm:px-6">
      <LoadingLabel>Loading the audit ledger…</LoadingLabel>
      <PageHeaderSkeleton />
      <StatsSkeleton />
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_344px]">
        <TableSkeleton rows={8} />
        <aside className="order-first flex flex-col gap-5 xl:order-none">
          <div className="glass rounded-xl p-4">
            <Skeleton className="h-4 w-36" />
            <Skeleton className="mt-4 h-[148px] w-full rounded-lg" />
          </div>
          <div className="glass rounded-xl p-4">
            <Skeleton className="h-4 w-28" />
            <div className="mt-4 grid grid-cols-4 gap-2">
              {Array.from({ length: 4 }, (_, index) => (
                <Skeleton key={index} className="h-11" />
              ))}
            </div>
            {Array.from({ length: 4 }, (_, index) => (
              <div key={index} className="mt-4 flex items-center justify-between gap-3">
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="h-3.5 w-16" />
              </div>
            ))}
          </div>
        </aside>
      </div>
    </main>
  );
}

const DIVIDER = ["", "border-t sm:border-l sm:border-t-0", "border-t xl:border-l xl:border-t-0", "border-t sm:border-l xl:border-t-0"];

/** The metrics strip (136px cells) or, `compact`, the archive summary (104px cells). */
export function StatsSkeleton({ compact = false }: { compact?: boolean }) {
  return (
    <div className="glass grid grid-cols-1 overflow-hidden rounded-xl sm:grid-cols-2 xl:grid-cols-4">
      {DIVIDER.map((divider, index) => (
        <div key={index} className={cn("flex flex-col justify-between border-line px-4 py-3.5", compact ? "min-h-[104px]" : "min-h-[136px]", divider)}>
          <Skeleton className="h-3 w-32" />
          <div className="flex items-end justify-between">
            <div>
              <Skeleton className="h-[26px] w-28" />
              <Skeleton className="mt-2.5 h-3 w-24" />
            </div>
            {compact ? null : <Skeleton className="h-8 w-24" />}
          </div>
          {compact ? null : <Skeleton className="h-3 w-36" />}
        </div>
      ))}
    </div>
  );
}

/** A table panel: toolbar, tab strip, header and fixed-height rows. */
export function TableSkeleton({ rows, className }: { rows: number; className?: string }) {
  return (
    <div className={cn("glass overflow-hidden rounded-xl", className)}>
      <div className="flex items-center justify-between gap-4 px-4 pb-3 pt-4">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-8 w-72 max-w-[40vw] rounded-lg" />
      </div>
      <div className="flex gap-4 border-b border-line px-4 pb-2.5">
        {[88, 72, 64, 40].map((width, index) => (
          <Skeleton key={index} className="h-4" style={{ width }} />
        ))}
      </div>
      <div className="h-9 border-b border-line" />
      {Array.from({ length: rows }, (_, index) => (
        <SkeletonRow key={index} index={index} />
      ))}
      <div className="flex h-11 items-center justify-between px-4">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-32" />
      </div>
    </div>
  );
}

export function SkeletonRow({ index, className }: { index: number; className?: string }) {
  // Varying widths read as content rather than a grid of identical bars.
  const vendor = [148, 196, 124, 172, 160, 210, 136, 184][index % 8];
  return (
    <div className={cn("flex h-12 items-center gap-6 border-b border-line px-4", className)}>
      <Skeleton className="h-3.5 w-24 shrink-0" />
      <Skeleton className="h-3.5 shrink-0" style={{ width: vendor }} />
      <Skeleton className="ml-auto h-3.5 w-20 shrink-0" />
      <Skeleton className="h-3.5 w-24 shrink-0" />
      <Skeleton className="h-[22px] w-24 shrink-0 rounded-full" />
    </div>
  );
}
