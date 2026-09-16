import { useId, useState, type PointerEvent } from "react";
import type { TrendPoint } from "@/state/metrics";

interface SparklineProps {
  points: TrendPoint[];
  format: (value: number) => string;
  label: string;
  width?: number;
  height?: number;
}

/**
 * 12-point trend in the de-emphasis ink with the current period marked in the accent.
 * Hover (or keyboard focus + arrows) reads any point; values are also in an sr-only list.
 */
export function Sparkline({ points, format, label, width = 116, height = 36 }: SparklineProps) {
  const [active, setActive] = useState<number | null>(null);
  const titleId = useId();
  // useId output contains characters that are awkward inside url(#…); keep the gradient id plain.
  const fillId = `spark-${titleId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  if (points.length < 2) return null;

  const padX = 5;
  const padTop = 6;
  const padBottom = 3;
  const values = points.map((p) => p.value);
  const max = Math.max(...values);
  const min = Math.min(0, ...values);
  const span = max - min || 1;
  const x = (index: number) => padX + (index * (width - padX * 2)) / (points.length - 1);
  const y = (value: number) => height - padBottom - ((value - min) / span) * (height - padTop - padBottom);
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${line} L${x(points.length - 1).toFixed(1)},${y(min).toFixed(1)} L${x(0).toFixed(1)},${y(min).toFixed(1)} Z`;
  const last = points.length - 1;
  const shown = active ?? last;
  const shownPoint = points[shown]!;

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - box.left) / box.width;
    setActive(Math.max(0, Math.min(last, Math.round(ratio * last))));
  };

  return (
    <div className="relative select-none">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-labelledby={titleId}
        tabIndex={0}
        className="block cursor-crosshair overflow-visible rounded-[3px] outline-offset-4"
        onPointerMove={onPointerMove}
        onPointerLeave={() => setActive(null)}
        onBlur={() => setActive(null)}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") setActive(Math.max(0, shown - 1));
          if (event.key === "ArrowRight") setActive(Math.min(last, shown + 1));
        }}
      >
        <title id={titleId}>{label}</title>
        <defs>
          <linearGradient id={fillId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--color-ink-3)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--color-ink-3)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1={padX} x2={width - padX} y1={y(min)} y2={y(min)} stroke="var(--color-line)" strokeWidth="1" />
        <path d={area} fill={`url(#${fillId})`} />
        <path d={line} fill="none" stroke="var(--color-ink-3)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {active !== null ? (
          <line x1={x(active)} x2={x(active)} y1={padTop - 4} y2={y(min)} stroke="var(--color-line-strong)" strokeWidth="1" />
        ) : null}
        <circle
          cx={x(shown)}
          cy={y(shownPoint.value)}
          r="4"
          fill={active === null || active === last ? "var(--color-accent)" : "var(--color-ink)"}
          stroke="var(--color-surface-solid)"
          strokeWidth="2"
        />
      </svg>
      <ul className="sr-only">
        {points.map((p) => (
          <li key={p.label}>
            {p.label}: {format(p.value)}
          </li>
        ))}
      </ul>
      {active !== null ? (
        <div
          className="pointer-events-none absolute bottom-full z-10 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-raised px-2 py-1 text-[11px] shadow-overlay"
          style={{ left: Math.min(Math.max(x(active), 40), width - 20) }}
        >
          <span className="text-ink-3">{shownPoint.label}</span> <span className="figure text-ink">{format(shownPoint.value)}</span>
        </div>
      ) : null}
    </div>
  );
}
