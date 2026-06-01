import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Skeleton placeholder for loading states.
 *
 * A muted, gently pulsing block. Size/shape via Tailwind classes
 * (`h-*`, `w-*`, `rounded-*`). Decorative by default (`aria-hidden`);
 * wrap groups in a container with `aria-busy`/`aria-live` where a status
 * needs announcing.
 */
export const Skeleton = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      aria-hidden="true"
      className={cn("bg-muted/70 animate-pulse rounded-md", className)}
      {...props}
    />
  ),
);
Skeleton.displayName = "Skeleton";
