import { CircleAlert, Info, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { formatAmount } from "@/lib/money";
import { riskBand, SEVERITY_RANK, type RiskBand } from "@/lib/rules";
import type { Amount, InvoiceStatus, Severity } from "@/lib/types";

/**
 * Emerald: approved. Amber: held for review. Crimson: rejected — a reviewer confirmed a
 * blocking (fraud or integrity) flag. The dot carries the hue; the label never relies on it.
 */
const STATUS: Record<InvoiceStatus, { label: string; tone: string; dot: string }> = {
  APPROVED: { label: "Approved", tone: "bg-approved/10 text-approved ring-approved/25", dot: "bg-approved" },
  NEEDS_REVIEW: { label: "Needs review", tone: "bg-review/10 text-review ring-review/30", dot: "bg-review" },
  REJECTED: { label: "Rejected", tone: "bg-rejected/10 text-rejected ring-rejected/25", dot: "bg-rejected" },
  PROCESSING: { label: "Processing", tone: "bg-ink-3/10 text-ink-2 ring-line-strong", dot: "bg-ink-3 animate-pulse" },
};

export function StatusBadge({ status, openFlags, className }: { status: InvoiceStatus; openFlags?: number; className?: string }) {
  const { label, tone, dot } = STATUS[status];
  return (
    <span
      className={cn(
        "inline-flex h-[22px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full pl-2 pr-2.5 text-[12px] font-medium ring-1 ring-inset",
        tone,
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full", dot)} aria-hidden />
      {label}
      {status === "NEEDS_REVIEW" && openFlags ? (
        <span className="figure -mr-1 rounded-full bg-review/15 px-1.5 text-[11px] leading-4" aria-label={`${openFlags} open flags`}>
          {openFlags}
        </span>
      ) : null}
    </span>
  );
}

export const SEVERITY: Record<Severity, { icon: LucideIcon; text: string; bg: string; border: string; paper: string }> = {
  CRITICAL: { icon: OctagonAlert, text: "text-sev-critical", bg: "bg-sev-critical", border: "border-sev-critical/40", paper: "var(--color-paper-critical)" },
  HIGH: { icon: TriangleAlert, text: "text-sev-high", bg: "bg-sev-high", border: "border-sev-high/40", paper: "var(--color-paper-high)" },
  MEDIUM: { icon: CircleAlert, text: "text-sev-medium", bg: "bg-sev-medium", border: "border-sev-medium/40", paper: "var(--color-paper-medium)" },
  LOW: { icon: Info, text: "text-sev-low", bg: "bg-sev-low", border: "border-sev-low/40", paper: "var(--color-paper-low)" },
};

/** Icon + label + a four-step pip meter, so severity never rests on color alone. */
export function SeverityTag({ severity, className }: { severity: Severity; className?: string }) {
  const { icon: Icon, text, bg } = SEVERITY[severity];
  const rank = SEVERITY_RANK[severity];
  return (
    <span className={cn("inline-flex items-center gap-1.5 font-label text-[11px] font-semibold uppercase tracking-[0.06em]", text, className)}>
      <Icon className="size-3.5" aria-hidden />
      {severity}
      <span className="flex items-end gap-[2px]" aria-hidden>
        {[1, 2, 3, 4].map((step) => (
          <span key={step} className={cn("w-[3px] rounded-[1px]", step <= rank ? bg : "bg-line-strong")} style={{ height: 4 + step * 2 }} />
        ))}
      </span>
    </span>
  );
}

const BAND: Record<RiskBand, { label: string; fill: string; track: string; text: string }> = {
  low: { label: "Low", fill: "bg-sev-low", track: "bg-sev-low/15", text: "text-ink-2" },
  moderate: { label: "Moderate", fill: "bg-sev-medium", track: "bg-sev-medium/15", text: "text-ink" },
  high: { label: "High", fill: "bg-sev-high", track: "bg-sev-high/15", text: "text-ink" },
  severe: { label: "Severe", fill: "bg-sev-critical", track: "bg-sev-critical/15", text: "text-ink" },
};

export function RiskMeter({ score, className }: { score: number | null; className?: string }) {
  if (score === null) return <span className="figure text-ink-3">—</span>;
  const band = BAND[riskBand(score)];
  return (
    <Tooltip
      content={
        <span>
          <span className="font-medium">{band.label} risk.</span> Combines open findings by severity and rule confidence, plus
          extraction uncertainty. Dismissed findings don't count.
        </span>
      }
    >
      <span className={cn("inline-flex items-center gap-2", className)} tabIndex={0} aria-label={`Risk ${score} of 100, ${band.label}`}>
        <span className={cn("figure w-6 text-right text-[12px]", band.text)}>{score}</span>
        <span className={cn("relative h-1 w-12 overflow-hidden rounded-full", band.track)} aria-hidden>
          <span className={cn("absolute inset-y-0 left-0 rounded-full", band.fill)} style={{ width: `${Math.max(score, 4)}%` }} />
        </span>
      </span>
    </Tooltip>
  );
}

export function Money({ amount, currency, code = true, className }: { amount: Amount | null; currency: string; code?: boolean; className?: string }) {
  if (amount === null) return <span className={cn("figure text-ink-3", className)}>—</span>;
  return (
    <span className={cn("figure whitespace-nowrap", className)}>
      {code ? <span className="mr-1.5 text-[11px] text-ink-3">{currency}</span> : null}
      {formatAmount(amount, currency)}
    </span>
  );
}

export function Kbd({ children, className }: { children: string; className?: string }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border border-line bg-sunken px-1 font-mono text-[10px] text-ink-3",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

export function Avatar({ name, className }: { name: string; className?: string }) {
  const initials = name
    .split(/\s+/)
    .map((part) => part[0])
    .slice(0, 2)
    .join("");
  return (
    <span
      className={cn(
        "inline-flex size-6 shrink-0 items-center justify-center rounded-full bg-accent/15 font-label text-[10.5px] font-semibold text-accent ring-1 ring-inset ring-accent/20",
        className,
      )}
      aria-hidden
    >
      {initials}
    </span>
  );
}
