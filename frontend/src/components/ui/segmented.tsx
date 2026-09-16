import { ToggleGroup } from "radix-ui";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

interface SegmentedProps<T extends string> {
  value: T;
  onValueChange: (value: T) => void;
  options: readonly { value: T; label: ReactNode; count?: number }[];
  /** Accessible name for the group. */
  label: string;
  className?: string;
}

/** A compact single-choice switch, for filters with a handful of options. */
export function Segmented<T extends string>({ value, onValueChange, options, label, className }: SegmentedProps<T>) {
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      // Radix reports "" when the active item is clicked again; keep the current choice.
      onValueChange={(next) => next && onValueChange(next as T)}
      aria-label={label}
      className={cn("inline-flex h-8 shrink-0 items-center gap-0.5 rounded-lg border border-line bg-sunken p-0.5", className)}
    >
      {options.map((option) => (
        <ToggleGroup.Item
          key={option.value}
          value={option.value}
          className={cn(
            "inline-flex h-full cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-[12px] font-medium text-ink-3 transition-[background-color,color,box-shadow] duration-150",
            "hover:text-ink-2 data-[state=on]:bg-surface-solid data-[state=on]:text-ink data-[state=on]:shadow-[var(--shadow-card),0_0_0_1px_var(--line)]",
          )}
        >
          {option.label}
          {option.count !== undefined ? <span className="figure min-w-[2ch] text-[11px] text-ink-3">{option.count.toLocaleString()}</span> : null}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}
