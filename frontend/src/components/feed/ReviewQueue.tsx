import { CheckCheck, Clock } from "lucide-react";
import { useMemo } from "react";
import { RiskMeter, SEVERITY } from "@/components/audit/status";
import { useNow } from "@/hooks/use-now";
import { cn } from "@/lib/cn";
import { DAY, relativeTime } from "@/lib/dates";
import { riskScore, SEVERITIES } from "@/lib/rules";
import { useAudit } from "@/state/audit-store";

/** Review items older than this are called out as aging. */
const QUEUE_AGE_WARNING_MS = 2 * DAY;
const SHOWN = 5;

/** What is waiting for a reviewer, highest risk first, with aging called out. */
export function ReviewQueue({ onOpen }: { onOpen: (invoiceId: string) => void }) {
  const { invoices } = useAudit();
  const now = useNow(60_000);
  const queue = useMemo(
    () =>
      invoices
        .filter((invoice) => invoice.status === "NEEDS_REVIEW")
        .map((invoice) => ({ invoice, risk: riskScore(invoice) ?? 0 }))
        .sort((a, b) => b.risk - a.risk || a.invoice.created_at.localeCompare(b.invoice.created_at)),
    [invoices],
  );
  const openBySeverity = SEVERITIES.map((severity) => ({
    severity,
    count: queue.reduce((sum, { invoice }) => sum + invoice.anomalies.filter((a) => a.status === "OPEN" && a.severity === severity).length, 0),
  }));
  const oldest = queue.reduce<(typeof queue)[number] | null>(
    (found, item) => (!found || item.invoice.created_at < found.invoice.created_at ? item : found),
    null,
  );

  return (
    <section aria-labelledby="queue-heading" className="glass overflow-hidden rounded-xl">
      <div className="flex items-baseline justify-between px-4 pb-3 pt-4">
        <h2 id="queue-heading" className="text-[15px] font-semibold tracking-[-0.01em]">
          Review queue
        </h2>
        <span className="text-[11px] text-ink-3">Highest risk first</span>
      </div>

      <dl className="grid grid-cols-4 border-y border-line">
        {openBySeverity.map(({ severity, count }, index) => {
          const { icon: Icon, text } = SEVERITY[severity];
          return (
            <div key={severity} className={cn("px-3 py-2.5", index > 0 && "border-l border-line")}>
              <dt className={cn("flex items-center gap-1 font-label text-[10px] font-semibold uppercase tracking-[0.06em]", count ? text : "text-ink-3")}>
                <Icon className="size-3" aria-hidden />
                {severity}
              </dt>
              <dd className={cn("figure mt-0.5 text-[18px] leading-tight", count ? "text-ink" : "text-ink-3")}>{count}</dd>
            </div>
          );
        })}
      </dl>

      {queue.length === 0 ? (
        <div className="flex flex-col items-center px-4 py-7 text-center">
          <CheckCheck className="size-5 text-approved" aria-hidden />
          <p className="mt-2 text-[13px] font-medium text-ink">Queue is clear</p>
          <p className="mt-0.5 text-[12px] text-ink-3">Nothing is waiting for a reviewer.</p>
        </div>
      ) : (
        <ol className="divide-y divide-line">
          {queue.slice(0, SHOWN).map(({ invoice, risk }) => {
            const aging = now - new Date(invoice.created_at).getTime() > QUEUE_AGE_WARNING_MS;
            return (
              <li key={invoice.invoice_id}>
                <button
                  type="button"
                  onClick={() => onOpen(invoice.invoice_id)}
                  className="grid h-[52px] w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] content-center items-center gap-x-3 gap-y-0.5 px-4 text-left transition-colors hover:bg-hover/60"
                >
                  <span className="truncate text-[13px] text-ink">{invoice.vendor.name ?? "Unknown vendor"}</span>
                  <RiskMeter score={risk} />
                  <span className="figure truncate text-[11px] text-ink-3">{invoice.invoice_number ?? "—"}</span>
                  <span className={cn("flex items-center justify-end gap-1 text-[11px]", aging ? "text-review" : "text-ink-3")}>
                    {aging ? <Clock className="size-3" aria-hidden /> : null}
                    {relativeTime(invoice.created_at, now)}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}

      {oldest ? (
        <p className="border-t border-line px-4 py-2.5 text-[11px] text-ink-3">
          {queue.length > SHOWN ? `${queue.length - SHOWN} more in the feed · ` : ""}Oldest waiting: {oldest.invoice.vendor.name ?? "Unknown vendor"},{" "}
          {relativeTime(oldest.invoice.created_at, now)}
        </p>
      ) : null}
    </section>
  );
}
