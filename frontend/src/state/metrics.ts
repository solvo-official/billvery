import { DAY, formatDayMonth } from "@/lib/dates";
import { convert, formatCompact } from "@/lib/money";
import { decideInvoiceStatus, isBlocking, isStraightThrough } from "@/lib/rules";
import type { InvoiceRecord, SettledStatus } from "@/lib/types";

export interface TrendPoint {
  label: string;
  value: number;
}

export interface MetricDelta {
  text: string;
  direction: "up" | "down" | "flat";
  tone: "good" | "bad" | "neutral";
}

export interface Metric {
  id: "volume" | "auto-approved" | "review" | "blocked";
  label: string;
  value: string;
  detail: string;
  delta: MetricDelta | null;
  comparison: string;
  trend: TrendPoint[];
  trendLabel: string;
  formatPoint: (value: number) => string;
  definition: string;
}

const time = (invoice: InvoiceRecord) => new Date(invoice.created_at).getTime();
const settled = (invoices: InvoiceRecord[]) => invoices.filter((i) => i.status !== "PROCESSING");
const inWindow = (invoices: InvoiceRecord[], from: number, to: number) =>
  invoices.filter((i) => time(i) >= from && time(i) < to);

/** The invoice's status as it stood at `at`, replaying resolutions made before then. */
export function statusAt(invoice: InvoiceRecord, at: number): SettledStatus | null {
  if (time(invoice) > at || invoice.status === "PROCESSING") return null;
  return decideInvoiceStatus(
    invoice.anomalies.map((a) => ({
      severity: a.severity,
      status: a.resolved_at && new Date(a.resolved_at).getTime() <= at ? a.status : "OPEN",
    })),
  );
}

function weeks(now: number, count = 12): { from: number; to: number; label: string }[] {
  return Array.from({ length: count }, (_, index) => {
    const to = now - (count - 1 - index) * 7 * DAY;
    const from = to - 7 * DAY;
    return { from, to, label: `Week to ${formatDayMonth(new Date(to))}` };
  });
}

function percentChange(current: number, previous: number): MetricDelta | null {
  if (previous === 0) return null;
  const change = ((current - previous) / previous) * 100;
  const direction = Math.abs(change) < 0.05 ? "flat" : change > 0 ? "up" : "down";
  return { text: `${change > 0 ? "+" : ""}${change.toFixed(1)}%`, direction, tone: "neutral" };
}

export function computeMetrics(invoices: InvoiceRecord[], currency: string, now: number): Metric[] {
  const current = settled(inWindow(invoices, now - 30 * DAY, now + DAY));
  const previous = settled(inWindow(invoices, now - 60 * DAY, now - 30 * DAY));
  const volumeOf = (list: InvoiceRecord[]) =>
    list.reduce((sum, i) => sum + convert(i.financial_summary.total ?? "0", i.financial_summary.currency, currency), 0);
  const money = (value: number) => formatCompact(value, currency);
  const weekly = weeks(now);

  // Total audited volume
  const volumeNow = volumeOf(current);
  const volume: Metric = {
    id: "volume",
    label: "Total audited volume",
    value: money(volumeNow),
    detail: `${current.length.toLocaleString()} invoices`,
    delta: percentChange(volumeNow, volumeOf(previous)),
    comparison: "vs prior 30 days",
    trend: weekly.map((w) => ({ label: w.label, value: volumeOf(settled(inWindow(invoices, w.from, w.to))) })),
    trendLabel: "weekly volume, 12 weeks",
    formatPoint: money,
    definition: current.some((i) => i.financial_summary.currency !== currency)
      ? `Invoice totals audited in the last 30 days, in ${currency}. Other currencies are converted at fixed reference rates.`
      : `Invoice totals audited in the last 30 days, in ${currency}.`,
  };

  // Auto-approved rate
  const rate = (list: InvoiceRecord[]) => (list.length ? (list.filter(isStraightThrough).length / list.length) * 100 : 0);
  const rateNow = rate(current);
  const ratePrev = rate(previous);
  const pts = rateNow - ratePrev;
  const autoApproved: Metric = {
    id: "auto-approved",
    label: "Auto-approved rate",
    value: `${rateNow.toFixed(1)}%`,
    detail: `${current.filter(isStraightThrough).length} of ${current.length} with no review`,
    delta: previous.length
      ? {
          text: `${pts > 0 ? "+" : ""}${pts.toFixed(1)} pts`,
          direction: Math.abs(pts) < 0.05 ? "flat" : pts > 0 ? "up" : "down",
          tone: Math.abs(pts) < 0.05 ? "neutral" : pts > 0 ? "good" : "bad",
        }
      : null,
    comparison: "vs prior 30 days",
    trend: weekly.map((w) => ({ label: w.label, value: rate(settled(inWindow(invoices, w.from, w.to))) })),
    trendLabel: "weekly rate, 12 weeks",
    formatPoint: (value) => `${value.toFixed(1)}%`,
    definition: "Share of invoices the rule engine approved with no HIGH or CRITICAL finding and no human decision.",
  };

  // Review queue
  const queueAt = (at: number) => invoices.filter((i) => statusAt(i, at) === "NEEDS_REVIEW").length;
  const queued = invoices.filter((i) => i.status === "NEEDS_REVIEW");
  const openFlags = queued.reduce((sum, i) => sum + i.anomalies.filter((a) => a.status === "OPEN" && isBlocking(a.severity)).length, 0);
  const queueNow = queued.length;
  const queueWeekAgo = queueAt(now - 7 * DAY);
  const queueDiff = queueNow - queueWeekAgo;
  const review: Metric = {
    id: "review",
    label: "Needs review",
    value: queueNow.toLocaleString(),
    detail: `${openFlags} open blocking flag${openFlags === 1 ? "" : "s"}`,
    delta: {
      text: `${queueDiff > 0 ? "+" : ""}${queueDiff}`,
      direction: queueDiff === 0 ? "flat" : queueDiff > 0 ? "up" : "down",
      tone: queueDiff === 0 ? "neutral" : queueDiff > 0 ? "bad" : "good",
    },
    comparison: "vs 7 days ago",
    trend: Array.from({ length: 12 }, (_, index) => {
      const at = now - (11 - index) * DAY;
      return { label: formatDayMonth(new Date(at)), value: queueAt(at) };
    }),
    trendLabel: "queue size, 12 days",
    formatPoint: (value) => `${value} in queue`,
    definition: "Invoices held for a reviewer: at least one HIGH or CRITICAL finding is still open.",
  };

  // Blocked
  const rejectedIn = (list: InvoiceRecord[]) => list.filter((i) => i.status === "REJECTED");
  const blockedNow = rejectedIn(current);
  const duplicates = blockedNow.filter((i) =>
    i.anomalies.some((a) => a.status === "CONFIRMED" && (a.type === "DUPLICATE_FILE_HASH" || a.type === "DUPLICATE_INVOICE_NUMBER")),
  ).length;
  const blocked: Metric = {
    id: "blocked",
    label: "Fraud & duplicates blocked",
    value: money(volumeOf(blockedNow)),
    detail: `${blockedNow.length} invoice${blockedNow.length === 1 ? "" : "s"} · ${duplicates} duplicate${duplicates === 1 ? "" : "s"}`,
    delta: percentChange(volumeOf(blockedNow), volumeOf(rejectedIn(previous))),
    comparison: "vs prior 30 days",
    trend: weekly.map((w) => ({ label: w.label, value: volumeOf(rejectedIn(settled(inWindow(invoices, w.from, w.to)))) })),
    trendLabel: "weekly blocked value, 12 weeks",
    formatPoint: money,
    definition: "Value of invoices a reviewer rejected by confirming a HIGH or CRITICAL finding, last 30 days.",
  };

  return [volume, autoApproved, review, blocked];
}
