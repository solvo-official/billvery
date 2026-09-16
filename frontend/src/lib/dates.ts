import type { IsoDate, IsoDateTime } from "./types";

export const MINUTE = 60_000;
export const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;

const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
const dateTimeFormat = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const weekFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const localDateFormat = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric" });

/** "2026-09-15" -> "15 Sep 2026" (calendar date, no time-zone shift) */
export const formatDate = (date: IsoDate) => dateFormat.format(new Date(`${date}T00:00:00Z`));
export const formatDateTime = (at: IsoDateTime) => dateTimeFormat.format(new Date(at));
export const formatDayMonth = (at: Date) => weekFormat.format(at);
/** The local calendar day of a timestamp: "15 Sep 2026". */
export const formatDay = (at: IsoDateTime) => localDateFormat.format(new Date(at));

export function toIsoDate(at: Date): IsoDate {
  return at.toISOString().slice(0, 10);
}

export function addDays(at: Date, days: number): Date {
  return new Date(at.getTime() + days * DAY);
}

export function relativeTime(at: IsoDateTime, now: number = Date.now()): string {
  const elapsed = Math.max(0, now - new Date(at).getTime());
  if (elapsed < MINUTE) return "just now";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)}m ago`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)}h ago`;
  return `${Math.floor(elapsed / DAY)}d ago`;
}
