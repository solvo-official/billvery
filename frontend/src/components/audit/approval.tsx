import { Bot, LoaderCircle } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { formatDateTime, relativeTime } from "@/lib/dates";
import type { Approval } from "@/lib/types";
import { Avatar } from "./status";

/** "Approved just now by Jane Doe" / "Approved automatically 2d ago" — the audit trail in one line. */
export function approvalSentence(approval: Approval, now: number): string {
  const when = relativeTime(approval.at, now);
  return approval.method === "automatic" ? `Approved automatically ${when}` : `Approved ${when} by ${approval.by?.name ?? "a reviewer"}`;
}

/** Inline stamp with the approver's avatar; the exact time is in the tooltip. */
export function ApprovalStamp({ approval, now, pending = false, className }: { approval: Approval; now: number; pending?: boolean; className?: string }) {
  return (
    <Tooltip content={<span className="figure">{formatDateTime(approval.at)}</span>}>
      <span className={cn("inline-flex min-w-0 items-center gap-1.5 text-[12px] text-ink-2", className)} tabIndex={0}>
        {approval.method === "automatic" ? (
          <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-ink-3/10 text-ink-3 ring-1 ring-inset ring-line" aria-hidden>
            <Bot className="size-3" />
          </span>
        ) : (
          <Avatar name={approval.by?.name ?? "?"} className="size-5 text-[9.5px]" />
        )}
        <span className="truncate">{approvalSentence(approval, now)}</span>
        {pending ? <LoaderCircle className="size-3 shrink-0 animate-spin text-ink-3" aria-label="Saving" /> : null}
      </span>
    </Tooltip>
  );
}
