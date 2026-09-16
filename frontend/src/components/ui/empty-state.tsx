import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: ReactNode;
  /** Calls to action, usually one primary and one secondary button. */
  children?: ReactNode;
  tone?: "neutral" | "approved" | "review";
  className?: string;
}

const TONE = {
  neutral: "text-ink-2",
  approved: "text-approved",
  review: "text-review",
} as const;

/** What a panel shows when there is nothing in it, and what to do about that. */
export function EmptyState({ icon: Icon, title, description, children, tone = "neutral", className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center px-6 py-12 text-center", className)}>
      <span
        className={cn(
          "relative flex size-11 items-center justify-center rounded-xl border border-line bg-surface-solid shadow-[var(--shadow-card)]",
          "before:absolute before:-inset-3 before:-z-10 before:rounded-2xl before:bg-[radial-gradient(closest-side,var(--ground-glow),transparent)]",
          TONE[tone],
        )}
        aria-hidden
      >
        <Icon className="size-5" strokeWidth={1.75} />
      </span>
      <h3 className="mt-4 text-[14px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
      {description ? <p className="mt-1 max-w-[46ch] text-[13px] leading-relaxed text-ink-2">{description}</p> : null}
      {children ? <div className="mt-4 flex flex-wrap items-center justify-center gap-2">{children}</div> : null}
    </div>
  );
}
