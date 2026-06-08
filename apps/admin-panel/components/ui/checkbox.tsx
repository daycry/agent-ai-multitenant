"use client";

import * as React from "react";
import { Check, Minus } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Checkbox primitive.
 *
 * A real, focusable `<input type="checkbox">` (so forms, labels via
 * `htmlFor`, and Playwright all keep working) with a styled overlay box
 * on top. The native input stays in the DOM — only visually hidden with
 * `sr-only` + `peer` — so every existing `data-testid`, `checked`,
 * `onChange`, `id`, etc. behave exactly like the bare element it replaces.
 *
 * Tri-state (Plan 06.18 task_06_18_09): pass `indeterminate` to render
 * the "partially selected" state used by group "select-all" headers. The
 * indeterminate flag is a DOM *property* (not an attribute), so it is
 * applied imperatively to the input and also reflected with
 * `aria-checked="mixed"` for assistive tech and a dash glyph in the box.
 */
export interface CheckboxProps extends React.InputHTMLAttributes<HTMLInputElement> {
  indeterminate?: boolean;
}

export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, indeterminate = false, checked, ...props }, ref) => {
    const innerRef = React.useRef<HTMLInputElement | null>(null);

    const setRefs = React.useCallback(
      (node: HTMLInputElement | null) => {
        innerRef.current = node;
        if (typeof ref === "function") ref(node);
        else if (ref) (ref as React.MutableRefObject<HTMLInputElement | null>).current = node;
      },
      [ref],
    );

    React.useEffect(() => {
      if (innerRef.current) innerRef.current.indeterminate = indeterminate;
    }, [indeterminate]);

    return (
      <span className="relative inline-flex h-4 w-4 shrink-0 items-center justify-center">
        <input
          type="checkbox"
          ref={setRefs}
          checked={checked}
          aria-checked={indeterminate ? "mixed" : undefined}
          className={cn(
            "peer absolute inset-0 cursor-pointer opacity-0 disabled:cursor-not-allowed",
          )}
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
            "peer-checked:[&>svg.check]:opacity-100",
            "peer-disabled:opacity-50",
            // Indeterminate paints the box like checked + shows the dash.
            indeterminate && "border-primary bg-primary text-primary-foreground",
            className,
          )}
        >
          <Check className="check h-3 w-3 opacity-0 transition-opacity" />
          {indeterminate && <Minus className="absolute h-3 w-3" />}
        </span>
      </span>
    );
  },
);
Checkbox.displayName = "Checkbox";
