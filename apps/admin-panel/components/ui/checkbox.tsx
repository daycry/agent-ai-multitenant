"use client";

import * as React from "react";
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Checkbox primitive.
 *
 * A real, focusable `<input type="checkbox">` (so forms, labels via
 * `htmlFor`, and Playwright all keep working) with a styled overlay box
 * on top. The native input stays in the DOM — only visually hidden with
 * `sr-only` + `peer` — so every existing `data-testid`, `checked`,
 * `onChange`, `id`, etc. behave exactly like the bare element it replaces.
 */
export type CheckboxProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, ...props }, ref) => (
    <span className="relative inline-flex h-4 w-4 shrink-0 items-center justify-center">
      <input
        type="checkbox"
        ref={ref}
        className={cn("peer absolute inset-0 cursor-pointer opacity-0 disabled:cursor-not-allowed")}
        {...props}
      />
      <span
        aria-hidden="true"
        className={cn(
          "border-input bg-background pointer-events-none flex h-4 w-4 items-center justify-center rounded border",
          "transition-colors",
          "peer-focus-visible:ring-ring peer-focus-visible:ring-2 peer-focus-visible:ring-offset-2",
          "peer-focus-visible:ring-offset-background",
          "peer-checked:border-primary peer-checked:bg-primary peer-checked:text-primary-foreground",
          "peer-checked:[&>svg]:opacity-100",
          "peer-disabled:opacity-50",
          className,
        )}
      >
        <Check className="h-3 w-3 opacity-0 transition-opacity" />
      </span>
    </span>
  ),
);
Checkbox.displayName = "Checkbox";
