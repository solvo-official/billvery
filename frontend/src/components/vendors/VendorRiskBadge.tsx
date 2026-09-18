import { ShieldAlert, ShieldCheck, ShieldHalf, type LucideIcon } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { relativeTime } from "@/lib/dates";
import type { VendorRiskTier, VendorScorecard } from "@/lib/types";
import { useAudit } from "@/state/audit-store";

/** Low stays quiet so the table isn't a wall of green; Medium and High carry the review hues. */
export const VENDOR_TIER: Record<VendorRiskTier, { label: string; tone: string; icon: LucideIcon; iconTone: string }> = {
  LOW: { label: "Low risk", tone: "bg-ink-3/[0.06] text-ink-2 ring-line", icon: ShieldCheck, iconTone: "text-approved" },
  MEDIUM: { label: "Medium risk", tone: "bg-review/10 text-review ring-review/30", icon: ShieldHalf, iconTone: "text-review" },
  HIGH: { label: "High risk", tone: "bg-rejected/10 text-rejected ring-rejected/25", icon: ShieldAlert, iconTone: "text-rejected" },
};

export const VENDOR_TIER_RANK: Record<VendorRiskTier, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };

interface VendorRiskBadgeProps {
  /** Looked up in the store's scorecards; pass `card` instead when you already hold it. */
  vendorId?: string | null;
  card?: VendorScorecard | null;
  /** sm fits a table row's second line. */
  size?: "sm" | "md";
  className?: string;
}

/** The vendor's automated risk tier; the tooltip shows the history behind it. */
export function VendorRiskBadge({ vendorId, card: given, size = "md", className }: VendorRiskBadgeProps) {
  const { vendorScorecard } = useAudit();
  const card = given ?? vendorScorecard(vendorId);
  if (!card) return null;
  const tier = VENDOR_TIER[card.risk.tier];
  const Icon = tier.icon;
  return (
    <Tooltip content={<VendorRiskSummary card={card} />} align="start">
      <span
        tabIndex={0}
        aria-label={`${tier.label} vendor: ${card.risk.reasons.join("; ")}`}
        className={cn(
          "inline-flex shrink-0 cursor-default items-center gap-1 whitespace-nowrap rounded-full font-medium ring-1 ring-inset",
          size === "sm" ? "h-4 pl-1 pr-1.5 text-[10.5px]" : "h-5 pl-1.5 pr-2 text-[11px]",
          tier.tone,
          className,
        )}
      >
        <Icon className={cn(size === "sm" ? "size-2.5" : "size-3", tier.iconTone)} aria-hidden />
        {tier.label}
      </span>
    </Tooltip>
  );
}

export function VendorRiskSummary({ card }: { card: VendorScorecard }) {
  const tier = VENDOR_TIER[card.risk.tier];
  return (
    <span className="flex flex-col gap-2 py-0.5">
      <span className="font-medium">
        {tier.label} · {card.name}
      </span>
      <span className="figure grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5 text-[11.5px]">
        <span className="font-sans text-ink-3">Invoices</span>
        <span className="text-right">{card.total_invoices.toLocaleString()}</span>
        <span className="font-sans text-ink-3">Held for review</span>
        <span className="text-right">
          {card.flag_rate_percent}% ({card.held_for_review})
        </span>
        <span className="font-sans text-ink-3">Possible duplicates</span>
        <span className="text-right">
          {card.duplicate_invoices}
          {card.confirmed_duplicates ? ` · ${card.confirmed_duplicates} confirmed` : ""}
        </span>
        <span className="font-sans text-ink-3">Rejected</span>
        <span className="text-right">{card.rejected_invoices}</span>
        <span className="font-sans text-ink-3">Last invoice</span>
        <span className="text-right font-sans">{relativeTime(card.last_invoice_at, Date.now())}</span>
      </span>
      <ul className="list-disc space-y-0.5 pl-4 text-[11.5px] text-ink-2">
        {card.risk.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </span>
  );
}
