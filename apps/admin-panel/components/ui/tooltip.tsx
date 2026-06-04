"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Tooltip primitive (Plan 06.18 task_06_18_09).
 *
 * Handcrafted (no Radix in this app — see package.json) but fully
 * accessible:
 *   - Opens on pointer hover AND on keyboard focus of the trigger, so a
 *     keyboard-only user gets the same affordance as a mouse user.
 *   - Closes on blur, mouse leave and Escape.
 *   - The trigger is wired with `aria-describedby` pointing at the
 *     tooltip panel, which carries `role="tooltip"`, so screen readers
 *     announce the help text when the trigger receives focus.
 *
 * The trigger MUST be a focusable element. The default trigger is a
 * `<button type="button">`; pass `asChild`-like content by composing
 * your own focusable element inside `<TooltipTrigger>` (it renders a
 * span by default and is given `tabIndex={0}` so it stays reachable).
 *
 * Usage:
 *   <Tooltip content="Help text in plain language">
 *     <TooltipTrigger>
 *       <Badge>…</Badge>
 *     </TooltipTrigger>
 *   </Tooltip>
 */

interface TooltipContextValue {
  open: boolean;
  show: () => void;
  hide: () => void;
  tooltipId: string;
}

const TooltipContext = React.createContext<TooltipContextValue | null>(null);

function useTooltipContext(component: string): TooltipContextValue {
  const ctx = React.useContext(TooltipContext);
  if (!ctx) {
    throw new Error(`${component} must be used within <Tooltip>`);
  }
  return ctx;
}

export interface TooltipProps {
  /** Help text (or rich node) shown in the floating panel. */
  content: React.ReactNode;
  children: React.ReactNode;
  /** Side the panel appears on relative to the trigger. */
  side?: "top" | "bottom";
  className?: string;
}

export function Tooltip({ content, children, side = "top", className }: TooltipProps) {
  const [open, setOpen] = React.useState(false);
  const reactId = React.useId();
  const tooltipId = `tooltip-${reactId}`;

  const show = React.useCallback(() => setOpen(true), []);
  const hide = React.useCallback(() => setOpen(false), []);

  const ctx = React.useMemo<TooltipContextValue>(
    () => ({ open, show, hide, tooltipId }),
    [open, show, hide, tooltipId],
  );

  return (
    <TooltipContext.Provider value={ctx}>
      <span
        className="relative inline-flex"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocusCapture={show}
        onBlurCapture={hide}
        onKeyDown={(e) => {
          if (e.key === "Escape") hide();
        }}
      >
        {children}
        <span
          role="tooltip"
          id={tooltipId}
          // Kept mounted (not conditionally removed) so `aria-describedby`
          // always resolves; visibility is driven purely by `open`.
          hidden={!open}
          className={cn(
            "bg-popover text-popover-foreground pointer-events-none absolute left-1/2 z-50",
            "w-max max-w-[16rem] -translate-x-1/2 rounded-md border px-2.5 py-1.5",
            "text-xs font-normal normal-case leading-snug shadow-md",
            side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
            className,
          )}
          data-state={open ? "open" : "closed"}
        >
          {content}
        </span>
      </span>
    </TooltipContext.Provider>
  );
}

export interface TooltipTriggerProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
}

/**
 * Focusable trigger wrapper. Renders a `<span tabIndex={0}>` wired with
 * `aria-describedby` so the floating panel is announced on focus. Because
 * informative badges are not interactive controls, a span with `tabIndex`
 * is the right semantic (it conveys "there is extra info here" without
 * pretending to be a button).
 */
export const TooltipTrigger = React.forwardRef<HTMLSpanElement, TooltipTriggerProps>(
  ({ children, className, ...props }, ref) => {
    const { tooltipId, open } = useTooltipContext("TooltipTrigger");
    return (
      <span
        ref={ref}
        tabIndex={0}
        aria-describedby={open ? tooltipId : undefined}
        className={cn(
          "focus-visible:ring-ring inline-flex rounded outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
          "focus-visible:ring-offset-background",
          className,
        )}
        {...props}
      >
        {children}
      </span>
    );
  },
);
TooltipTrigger.displayName = "TooltipTrigger";
