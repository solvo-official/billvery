import { ArrowRight, CircleCheck, LoaderCircle, Stamp } from "lucide-react";
import { ApprovalStamp } from "@/components/audit/approval";
import { Avatar, Kbd, StatusBadge } from "@/components/audit/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { isMac } from "@/hooks/use-hotkey";
import type { Approval, InvoiceStatus, SettledStatus, User } from "@/lib/types";

export const MIN_NOTE_LENGTH = 8;

export interface CommitReceipt {
  decisions: number;
  status: InvoiceStatus;
}

interface ResolutionPanelProps {
  openCount: number;
  decidedCount: number;
  outcome: SettledStatus;
  note: string;
  onNoteChange: (note: string) => void;
  onCommit: () => void;
  onClear: () => void;
  committing: boolean;
  reviewer: User | null;
  /** Why no reviewer is available, when none is. */
  reviewBlocker: string | null;
  receipt: CommitReceipt | null;
  approval: Approval | null;
  /** approved while this drawer was open */
  approvedHere: boolean;
  /** the approval is still being confirmed by the server */
  saving: boolean;
  now: number;
  nextInQueue: { label: string; onGo: () => void } | null;
}

const OUTCOME_EXPLAINER: Record<SettledStatus, string> = {
  APPROVED: "No HIGH or CRITICAL finding will remain open.",
  NEEDS_REVIEW: "A HIGH or CRITICAL finding stays open, so the invoice stays in the queue.",
  REJECTED: "A confirmed HIGH or CRITICAL finding rejects the invoice.",
};

export function ResolutionPanel(props: ResolutionPanelProps) {
  const { openCount, decidedCount, outcome, note, onNoteChange, onCommit, onClear, committing, reviewer, receipt, nextInQueue } = props;
  const { approval, approvedHere, saving, now } = props;
  const noteReady = note.trim().length >= MIN_NOTE_LENGTH;
  const ready = reviewer !== null && decidedCount > 0 && noteReady && !committing;
  const blocker = !reviewer
    ? props.reviewBlocker ?? "No reviewer is available."
    : decidedCount === 0
      ? "Dismiss or confirm at least one flag."
      : !noteReady
        ? "Add a resolution note for the audit log."
        : null;

  if (openCount === 0) {
    const acted = receipt !== null || approvedHere;
    return (
      <div className="flex min-h-[60px] flex-wrap items-center gap-x-3 gap-y-2 border-t border-line bg-sunken/50 px-4 py-3.5">
        {receipt && !approvedHere ? (
          <>
            <CircleCheck className="size-4 text-approved" aria-hidden />
            <p className="text-[13px] text-ink">
              Committed {receipt.decisions} decision{receipt.decisions === 1 ? "" : "s"}.
            </p>
            <StatusBadge status={receipt.status} />
          </>
        ) : approval ? (
          <ApprovalStamp approval={approval} now={now} pending={saving} className="text-[13px]" />
        ) : (
          <p className="text-[12px] text-ink-3">Every finding on this invoice has been resolved. Resolutions are final and can't be edited.</p>
        )}
        {acted ? (
          nextInQueue ? (
            <Button size="sm" variant="primary" className="ml-auto" onClick={nextInQueue.onGo}>
              Next in queue: {nextInQueue.label} <ArrowRight aria-hidden />
            </Button>
          ) : (
            <span className="ml-auto text-[12px] text-ink-3">The review queue is clear.</span>
          )
        ) : null}
      </div>
    );
  }

  return (
    <div className="border-t border-line bg-sunken/50 px-4 pb-3.5 pt-3">
      {receipt ? (
        <p className="mb-2.5 flex items-center gap-2 text-[12px] text-ink-2">
          <CircleCheck className="size-3.5 text-approved" aria-hidden />
          Committed {receipt.decisions} decision{receipt.decisions === 1 ? "" : "s"}; {openCount} flag{openCount === 1 ? "" : "s"} still open.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5">
        <p className="text-[12px] text-ink-3">
          <span className="figure text-ink">{decidedCount}</span> of <span className="figure text-ink">{openCount}</span> open flag{openCount === 1 ? "" : "s"} decided
        </p>
        <Tooltip content={OUTCOME_EXPLAINER[outcome]}>
          <p className="flex items-center gap-2 text-[12px] text-ink-3" tabIndex={0}>
            On commit <ArrowRight className="size-3.5" aria-hidden /> <StatusBadge status={outcome} />
          </p>
        </Tooltip>
      </div>

      <label htmlFor="resolution-note" className="eyebrow mt-3 block">
        Resolution note
      </label>
      <textarea
        id="resolution-note"
        rows={2}
        value={note}
        disabled={committing}
        onChange={(event) => onNoteChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && ready) {
            event.preventDefault();
            onCommit();
          }
        }}
        placeholder={
          outcome === "REJECTED"
            ? "What confirmed it? e.g. Called the vendor on the number in the vendor master; they never issued this invoice."
            : "What did you check? e.g. Totals re-added from the PDF; the difference is the vendor's freight surcharge."
        }
        className="mt-1.5 w-full resize-y rounded-lg border border-line bg-surface-solid px-2.5 py-2 text-[13px] leading-snug text-ink placeholder:text-ink-3 transition-[border-color,box-shadow] focus:border-accent/60 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_18%,transparent)] focus:outline-none disabled:opacity-60"
      />

      <div className="mt-2.5 flex flex-wrap items-center justify-between gap-3">
        {reviewer ? (
          <div className="flex min-w-0 items-center gap-2 text-[11px] text-ink-3">
            <Avatar name={reviewer.name} className="size-5 text-[9.5px]" />
            <span className="truncate">
              Signed as <span className="text-ink-2">{reviewer.name}</span>
            </span>
            <Tooltip content={<span className="figure">{reviewer.id}</span>}>
              <span className="figure cursor-default rounded-[4px] border border-line px-1 text-ink-3" tabIndex={0}>
                {reviewer.id.slice(0, 8)}
              </span>
            </Tooltip>
          </div>
        ) : (
          <span className="text-[11px] text-review">No reviewer selected</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={onClear} disabled={decidedCount === 0 || committing}>
            Clear
          </Button>
          <Button variant={outcome === "REJECTED" ? "danger" : "primary"} disabled={!ready} onClick={onCommit}>
            {committing ? <LoaderCircle className="animate-spin" aria-hidden /> : <Stamp aria-hidden />}
            {committing ? "Committing…" : "Resolve & Commit Audit"}
            {!committing ? <Kbd className="ml-1 border-white/20 bg-black/10 text-current opacity-80">{isMac ? "⌘↵" : "Ctrl ↵"}</Kbd> : null}
          </Button>
        </div>
      </div>
      {blocker ? <p className="mt-1.5 text-right text-[11px] text-ink-3">{blocker}</p> : null}
    </div>
  );
}
