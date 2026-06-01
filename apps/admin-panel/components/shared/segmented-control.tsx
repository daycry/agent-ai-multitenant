"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * SegmentedControl — the recurring "pick one of a few" toggle row that the
 * dashboards repeat by hand (window 7/30/90, currency USD/EUR/GBP, …): a
 * group of small pill buttons where exactly one is active.
 *
 * Presentation only. It is fully controlled (`value` + `onChange`) so the
 * owning page keeps its state, query keys and per-option `data-testid`s —
 * pass `getOptionTestId` to forward the exact testid each option had as a
 * bare `<button>`. Rendered as a real `role="radiogroup"` of
 * `role="radio"` buttons so keyboard + screen readers announce the choice.
 *
 *   <SegmentedControl
 *     label="Ventana:"
 *     value={windowDays}
 *     onChange={setWindowDays}
 *     options={WINDOW_OPTIONS.map((w) => ({ value: w, label: `${w}d` }))}
 *     getOptionTestId={(w) => `window-${w}`}
 *     data-testid="window-selector"
 *   />
 */
export interface SegmentedOption<T> {
  value: T;
  label: React.ReactNode;
  /** Optional accessible label when `label` is not plain text. */
  ariaLabel?: string;
}

interface SegmentedControlProps<T extends string | number> {
  /** Optional muted lead-in label rendered before the pills. */
  label?: React.ReactNode;
  value: T;
  onChange: (value: T) => void;
  options: ReadonlyArray<SegmentedOption<T>>;
  /** Forward each option's `data-testid` (keeps e2e selectors stable). */
  getOptionTestId?: (value: T) => string | undefined;
  className?: string;
  "data-testid"?: string;
  /** ARIA label for the whole group (defaults to a stringified `label`). */
  "aria-label"?: string;
}

export function SegmentedControl<T extends string | number>({
  label,
  value,
  onChange,
  options,
  getOptionTestId,
  className,
  ...props
}: SegmentedControlProps<T>) {
  const groupLabel =
    props["aria-label"] ?? (typeof label === "string" ? label.replace(/:$/, "") : undefined);

  return (
    <div className={cn("flex items-center gap-2", className)} data-testid={props["data-testid"]}>
      {label !== undefined && <span className="text-muted-foreground text-sm">{label}</span>}
      <div
        role="radiogroup"
        aria-label={groupLabel}
        className="bg-muted/60 inline-flex items-center gap-0.5 rounded-lg p-0.5"
      >
        {options.map((opt) => {
          const active = opt.value === value;
          return (
            <button
              key={String(opt.value)}
              type="button"
              role="radio"
              aria-checked={active}
              aria-label={opt.ariaLabel}
              onClick={() => onChange(opt.value)}
              data-testid={getOptionTestId?.(opt.value)}
              className={cn(
                "rounded-md px-3 py-1 text-sm font-medium transition-colors",
                "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-1",
                "focus-visible:ring-offset-background focus-visible:outline-none",
                active
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
