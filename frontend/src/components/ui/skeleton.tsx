import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

/**
 * A shimmering placeholder. Size it exactly like the content it stands in for (same height,
 * a realistic width) so nothing moves when the real content arrives.
 */
export function Skeleton({ className, ...props }: ComponentProps<"span">) {
  return <span aria-hidden className={cn("skeleton block", className)} {...props} />;
}

/** Screen-reader announcement for a region that is loading. */
export function LoadingLabel({ children }: { children: string }) {
  return (
    <span role="status" className="sr-only">
      {children}
    </span>
  );
}
