"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Select primitive.
 *
 * A styled wrapper around the native `<select>` so it matches the Input
 * primitive (same height, border, radius, focus ring) while keeping full
 * native behaviour: `value`/`defaultValue`/`onChange`, `<option>` and
 * `<optgroup>` children, keyboard, `data-testid`, etc. We add a chevron
 * affordance and hide the platform arrow. Children are the `<option>`s.
 */
export type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement>;

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => (
    <div className="relative w-full">
      <select
        ref={ref}
        className={cn(
          "border-input bg-background flex h-10 w-full appearance-none rounded-md border px-3 py-2 pr-9 text-sm",
          "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
          "focus-visible:ring-offset-background",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        className="text-muted-foreground pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2"
        aria-hidden="true"
      />
    </div>
  ),
);
Select.displayName = "Select";
