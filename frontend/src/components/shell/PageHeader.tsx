import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";

/** Title row shared by both workspaces; fixed height so skeleton and page line up exactly. */
export function PageHeader({ title, description, actions }: { title: string; description: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex min-h-[52px] flex-wrap items-end justify-between gap-x-6 gap-y-2">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold leading-7 tracking-[-0.02em] text-ink">{title}</h1>
        <p className="mt-1 truncate text-[13px] leading-5 text-ink-3">{description}</p>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function PageHeaderSkeleton() {
  return (
    <div className="flex min-h-[52px] items-end">
      <div>
        <Skeleton className="h-[22px] w-56" />
        <Skeleton className="mt-2.5 h-3.5 w-80 max-w-[70vw]" />
      </div>
    </div>
  );
}
