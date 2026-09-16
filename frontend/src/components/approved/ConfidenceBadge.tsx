import { ShieldAlert, ShieldCheck, ShieldQuestion, type LucideIcon } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { plural } from "@/lib/format";
import { isBlocking } from "@/lib/rules";
import type { InvoiceRecord } from "@/lib/types";

/** The rule engine's auto-approval threshold for extraction confidence. */
const AUTO_APPROVE_CONFIDENCE = 90;

type Band = "high" | "good" | "low" | "unknown";

const BAND: Record<Band, { tone: string; icon: LucideIcon; label: string }> = {
  high: { tone: "bg-approved/10 text-approved ring-approved/25", icon: ShieldCheck, label: "High confidence" },
  good: { tone: "bg-approved/[0.06] text-approved ring-approved/20", icon: ShieldCheck, label: "Above threshold" },
  low: { tone: "bg-review/10 text-review ring-review/30", icon: ShieldAlert, label: "Below threshold" },
  unknown: { tone: "bg-ink-3/10 text-ink-3 ring-line", icon: ShieldQuestion, label: "Not reported" },
};

function band(confidence: number | null): Band {
  if (confidence === null) return "unknown";
  if (confidence >= 95) return "high";
  if (confidence >= AUTO_APPROVE_CONFIDENCE) return "good";
  return "low";
}

/**
 * Gemini's extraction confidence, with a marker when a reviewer overrode flags to approve.
 * Color follows confidence; the override count is spelled out, never implied by color.
 */
export function ConfidenceBadge({ invoice, className }: { invoice: InvoiceRecord; className?: string }) {
  const confidence = invoice.ai_confidence;
  const { tone, icon: Icon, label } = BAND[band(confidence)];
  const dismissed = invoice.anomalies.filter((a) => a.status === "DISMISSED");
  const blocking = dismissed.filter((a) => isBlocking(a.severity)).length;

  return (
    <Tooltip
      content={
        <span className="flex flex-col gap-1">
          <span>
            <span className="font-medium">{label}.</span> Gemini read this document with{" "}
            {confidence === null ? "no reported confidence" : <span className="figure">{confidence}%</span>} confidence; the auto-approval threshold is{" "}
            <span className="figure">{AUTO_APPROVE_CONFIDENCE}%</span>.
          </span>
          <span className="text-ink-2">
            {dismissed.length === 0
              ? invoice.anomalies.length === 0
                ? "No audit rule flagged it."
                : "Its flags were informational; none were dismissed."
              : `${plural(dismissed.length, "flag")} dismissed by a reviewer${blocking ? `, ${blocking} of them HIGH or CRITICAL` : ""}.`}
          </span>
        </span>
      }
    >
      <span className={cn("inline-flex items-center gap-1.5", className)} tabIndex={0}>
        <span className={cn("inline-flex h-[22px] min-w-[62px] items-center gap-1 rounded-full pl-1.5 pr-2 text-[12px] font-medium ring-1 ring-inset", tone)}>
          <Icon className="size-3.5" aria-hidden />
          <span className="figure">{confidence === null ? "—" : `${confidence}%`}</span>
        </span>
        {dismissed.length > 0 ? (
          <span
            className={cn(
              "figure inline-flex h-[22px] items-center rounded-full px-1.5 text-[11px] ring-1 ring-inset",
              blocking ? "text-review ring-review/30" : "text-ink-3 ring-line",
            )}
            aria-label={`${dismissed.length} flags overridden`}
          >
            {dismissed.length}×
          </span>
        ) : null}
      </span>
    </Tooltip>
  );
}
