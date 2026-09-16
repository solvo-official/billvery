/**
 * Exact decimal arithmetic for API amounts (NUMERIC(18,4) strings). Amounts are held as
 * bigint "units" of 1/10,000 so sums and comparisons never touch floating point.
 */
import type { Amount } from "./types";

const SCALE_DIGITS = 4;
const SCALE = 10n ** BigInt(SCALE_DIGITS);

export function toUnits(amount: Amount): bigint {
  const match = /^(-)?(\d+)(?:\.(\d+))?$/.exec(amount.trim());
  if (!match) throw new Error(`Not a decimal amount: ${amount}`);
  const [, sign, whole = "0", fraction = ""] = match;
  const units = BigInt(whole) * SCALE + BigInt((fraction + "0000").slice(0, SCALE_DIGITS));
  return sign ? -units : units;
}

/** Same rendering as the backend: 2-4 decimals, no grouping. 14663.4 -> "14663.40". */
export function fromUnits(units: bigint): Amount {
  const negative = units < 0n;
  const abs = negative ? -units : units;
  const fraction = (abs % SCALE).toString().padStart(SCALE_DIGITS, "0").replace(/0+$/, "").padEnd(2, "0");
  return `${negative ? "-" : ""}${abs / SCALE}.${fraction}`;
}

export const centsToAmount = (cents: number): Amount => fromUnits(BigInt(Math.round(cents)) * 100n);

export function sumAmounts(amounts: Iterable<Amount | null | undefined>): Amount {
  let total = 0n;
  for (const amount of amounts) if (amount) total += toUnits(amount);
  return fromUnits(total);
}

export const subtractAmounts = (a: Amount, b: Amount): Amount => fromUnits(toUnits(a) - toUnits(b));
export const isZero = (amount: Amount | null) => amount === null || toUnits(amount) === 0n;

const minorDigitsCache = new Map<string, number>();

/** Decimal places a currency uses: USD 2, KWD 3, JPY 0. */
export function minorDigits(currency: string): number {
  let digits = minorDigitsCache.get(currency);
  if (digits === undefined) {
    try {
      digits = new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
    } catch {
      digits = 2;
    }
    minorDigitsCache.set(currency, digits);
  }
  return digits;
}

/** Exact, grouped rendering in the currency's own precision: "14663.4" -> "14,663.40". */
export function formatAmount(amount: Amount, currency: string): string {
  const digits = minorDigits(currency);
  const units = toUnits(amount);
  const negative = units < 0n;
  const step = 10n ** BigInt(SCALE_DIGITS - digits);
  const rounded = ((negative ? -units : units) + step / 2n) / step;
  const base = 10n ** BigInt(digits);
  const whole = (rounded / base).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = digits > 0 ? `.${(rounded % base).toString().padStart(digits, "0")}` : "";
  return `${negative ? "−" : ""}${whole}${fraction}`;
}

const compactCache = new Map<string, Intl.NumberFormat>();

/** Headline figures: $4.82M, AED 612.4K. Approximate by design. */
export function formatCompact(value: number, currency: string): string {
  let format = compactCache.get(currency);
  if (!format) {
    format = new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
      notation: "compact",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    compactCache.set(currency, format);
  }
  return format.format(value);
}

/** Fixed reference rates (USD per unit) for cross-currency totals. Display only. */
const USD_PER_UNIT: Record<string, number> = { USD: 1, EUR: 1.09, GBP: 1.27, AED: 0.2723, CAD: 0.73 };

export function convert(amount: Amount, from: string, to: string): number {
  if (from === to) return Number(amount);
  return (Number(amount) * (USD_PER_UNIT[from] ?? 1)) / (USD_PER_UNIT[to] ?? 1);
}
