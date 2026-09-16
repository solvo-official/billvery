import { ArrowDownRight, ArrowUpRight, Info, Minus } from "lucide-react";
import { useMemo } from "react";
import { Tooltip } from "@/components/ui/tooltip";
import { useNow } from "@/hooks/use-now";
import { cn } from "@/lib/cn";
import { MINUTE } from "@/lib/dates";
import { computeMetrics, type Metric, type MetricDelta } from "@/state/metrics";
import { useAudit, useOrganization } from "@/state/audit-store";
import { Sparkline } from "./Sparkline";

/** Hairlines between cells at every column count (1, 2 or 4 across). */
const DIVIDER = ["", "border-t sm:border-l sm:border-t-0", "border-t xl:border-l xl:border-t-0", "border-t sm:border-l xl:border-t-0"];

/** The four headline figures, computed from the live ledger, as one frosted strip. */
export function StatsRibbon() {
  const { invoices } = useAudit();
  const { currency_code: currency } = useOrganization();
  const now = useNow(MINUTE);
  const metrics = useMemo(() => computeMetrics(invoices, currency, now), [invoices, currency, now]);

  return (
    <section aria-label="Audit metrics" className="glass grid grid-cols-1 overflow-hidden rounded-xl sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map((metric, index) => (
        <MetricCell key={metric.id} metric={metric} className={DIVIDER[index]} />
      ))}
    </section>
  );
}

function MetricCell({ metric, className }: { metric: Metric; className?: string }) {
  return (
    <div className={cn("flex min-h-[136px] min-w-0 flex-col justify-between gap-2 border-line px-4 py-3.5", className)}>
      <div className="flex items-center gap-1.5">
        <h2 className="eyebrow">{metric.label}</h2>
        <Tooltip content={metric.definition}>
          <button type="button" className="rounded-full text-ink-3 transition-colors hover:text-ink-2" aria-label={`About ${metric.label}`}>
            <Info className="size-3" />
          </button>
        </Tooltip>
        <span className="ml-auto text-[11px] text-ink-3">{metric.id === "review" ? "now" : "30 days"}</span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[26px] font-medium leading-none tracking-[-0.03em] text-ink">{metric.value}</p>
          <p className="mt-2 truncate text-[12px] text-ink-2">{metric.detail}</p>
        </div>
        <Sparkline points={metric.trend} format={metric.formatPoint} label={`${metric.label}, ${metric.trendLabel}`} />
      </div>
      <p className="flex items-center gap-1.5 text-[12px] text-ink-3">
        {metric.delta ? <DeltaChip delta={metric.delta} /> : <span>No prior period</span>}
        <span>{metric.comparison}</span>
      </p>
    </div>
  );
}

const DELTA_TONE: Record<MetricDelta["tone"], string> = {
  good: "bg-approved/10 text-approved",
  bad: "bg-rejected/10 text-rejected",
  neutral: "bg-ink-3/10 text-ink-2",
};

function DeltaChip({ delta }: { delta: MetricDelta }) {
  const Icon = delta.direction === "up" ? ArrowUpRight : delta.direction === "down" ? ArrowDownRight : Minus;
  return (
    <span className={cn("inline-flex h-5 items-center gap-0.5 rounded-full pl-1 pr-1.5 font-mono text-[11px]", DELTA_TONE[delta.tone])}>
      <Icon className="size-3" aria-hidden />
      {delta.text}
    </span>
  );
}
