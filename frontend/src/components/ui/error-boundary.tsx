import { RotateCw, TriangleAlert } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Names the region in the fallback: "The audit feed couldn't be displayed". */
  label: string;
  /** When any of these change, a failed region tries again (e.g. the invoice it shows). */
  resetKeys?: readonly unknown[];
  className?: string;
  fallback?: (props: { error: Error; reset: () => void }) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Contains a render failure to the region it happened in. API errors are handled where the
 * requests are made and never reach here; this catches what's left (an unexpected record shape,
 * a bug), so one broken panel never blanks the whole console.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(`[${this.props.label}]`, error, info.componentStack);
  }

  componentDidUpdate(previous: ErrorBoundaryProps) {
    if (!this.state.error) return;
    const before = previous.resetKeys ?? [];
    const after = this.props.resetKeys ?? [];
    if (before.length !== after.length || before.some((key, index) => !Object.is(key, after[index]))) this.reset();
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback({ error, reset: this.reset });
    return <PanelError label={this.props.label} error={error} onRetry={this.reset} className={this.props.className} />;
  }
}

export function PanelError({ label, error, onRetry, className }: { label: string; error: Error; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("glass flex items-start gap-3 rounded-xl px-4 py-4", className)}>
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-rejected/10 text-rejected" aria-hidden>
        <TriangleAlert className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-ink">{label} couldn't be displayed</p>
        <p className="mt-0.5 text-[12px] text-ink-2">The rest of the console still works. Your data is unaffected.</p>
        <p className="figure mt-2 truncate text-[11px] text-ink-3" title={error.message}>
          {error.message}
        </p>
      </div>
      {onRetry ? (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          <RotateCw aria-hidden /> Try again
        </Button>
      ) : null}
    </div>
  );
}
