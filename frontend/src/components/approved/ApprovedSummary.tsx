import { BadgeCheck, Banknote, Gauge, UserCheck, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { formatCompact } from "@/lib/money";
import type { ArchiveSummary } from "./archive";

const DIVIDER = ["", "border-t sm:border-l sm:border-t-0", "border-t xl:border-l xl:border-t-0", "border-t sm:border-l xl:border-t-0"];

interface Card {
  label: string;
  icon: LucideIcon;
  value: ReactNode;
  detail: ReactNode;
}

/** Headline figures for the bills currently shown (they follow the filters). */
export function ApprovedSummary({ summary, currency, scope, loading }: { summary: ArchiveSummary; currency: string; scope: string; loading: boolean }) {
  const reviewerShare = summary.count ? Math.round((summary.reviewer / summary.count) * 100) : 0;
  const cards: Card[] = [
    {
      label: "Approved value",
      icon: Banknote,
      value: formatCompact(summary.value, currency),
      detail: summary.mixedCurrencies ? `${scope} · converted to ${currency}` : scope,
    },
    {
      label: "Bills approved",
      icon: BadgeCheck,
      value: summary.count.toLocaleString(),
      detail: `${summary.automatic.toLocaleString()} automatic · ${summary.reviewer.toLocaleString()} by reviewers`,
    },
    {
      label: "Human-reviewed",
      icon: UserCheck,
      value: `${reviewerShare}%`,
      detail: (
        <>
          <span className="figure">{summary.overridden}</span> flag{summary.overridden === 1 ? "" : "s"} overridden
          {summary.overriddenBlocking ? (
            <span className="text-review">
              {" "}
              · <span className="figure">{summary.overriddenBlocking}</span> blocking
            </span>
          ) : null}
        </>
      ),
    },
    {
      label: "Avg. extraction confidence",
      icon: Gauge,
      value: summary.averageConfidence === null ? "—" : `${summary.averageConfidence.toFixed(1)}%`,
      detail: "Gemini, across the bills shown",
    },
  ];

  return (
    <section aria-label="Approved bills summary" className="glass grid grid-cols-1 overflow-hidden rounded-xl sm:grid-cols-2 xl:grid-cols-4">
      {cards.map((card, index) => (
        <div key={card.label} className={cn("flex min-h-[104px] min-w-0 flex-col justify-between gap-2 border-line px-4 py-3.5", DIVIDER[index])}>
          <div className="flex items-center gap-1.5">
            <card.icon className="size-3.5 text-ink-3" aria-hidden />
            <h2 className="eyebrow">{card.label}</h2>
          </div>
          {loading ? (
            <div>
              <Skeleton className="h-[26px] w-24" />
              <Skeleton className="mt-2.5 h-3 w-40" />
            </div>
          ) : (
            <div className="min-w-0 animate-fade-in">
              <p className="font-mono text-[26px] font-medium leading-none tracking-[-0.03em] text-ink">{card.value}</p>
              <p className="mt-2 truncate text-[12px] text-ink-2">{card.detail}</p>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
