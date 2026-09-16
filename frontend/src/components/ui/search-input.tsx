import { Search, X } from "lucide-react";
import { useRef } from "react";
import { Kbd } from "@/components/audit/status";
import { useHotkey } from "@/hooks/use-hotkey";
import { cn } from "@/lib/cn";

interface SearchInputProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** Accessible name. */
  label: string;
  /** Focus with "/" from anywhere on the page. */
  hotkey?: boolean;
  className?: string;
}

export function SearchInput({ id, value, onChange, placeholder, label, hotkey = true, className }: SearchInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  useHotkey(
    "/",
    (event) => {
      event.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    },
    { enabled: hotkey },
  );

  return (
    <div className={cn("group relative", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-3 transition-colors group-focus-within:text-ink-2" aria-hidden />
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        ref={inputRef}
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            event.stopPropagation();
            onChange("");
          }
        }}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        className={cn(
          "h-8 w-full rounded-lg border border-line bg-surface-solid pl-8 pr-8 text-[13px] text-ink shadow-[var(--shadow-card)] placeholder:text-ink-3",
          "transition-[border-color,box-shadow] duration-150 [&::-webkit-search-cancel-button]:hidden",
          "hover:border-line-strong focus:border-accent/60 focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_18%,transparent)] focus:outline-none",
        )}
      />
      {value ? (
        <button
          type="button"
          onClick={() => {
            onChange("");
            inputRef.current?.focus();
          }}
          className="absolute right-1.5 top-1/2 flex size-5 -translate-y-1/2 items-center justify-center rounded text-ink-3 hover:bg-hover hover:text-ink"
          aria-label="Clear search"
        >
          <X className="size-3.5" />
        </button>
      ) : hotkey ? (
        <Kbd className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2">/</Kbd>
      ) : null}
    </div>
  );
}
