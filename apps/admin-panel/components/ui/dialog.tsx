"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Minimal controlled modal dialog. No portal: rendered inline at the
 * caller's tree position and absolutely positioned over the viewport.
 * That keeps it Playwright-friendly (no portal target juggling) and
 * avoids pulling in @radix-ui/react-dialog at this stage.
 *
 * Esc key closes; clicking the backdrop closes; focus traps lightly
 * via aria-modal. If we hit accessibility issues we'll swap in Radix.
 */

export type DialogSize = "sm" | "md" | "lg" | "xl" | "2xl";

interface DialogProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  size?: DialogSize;
  children: React.ReactNode;
}

const SIZE_CLASS: Record<DialogSize, string> = {
  sm: "max-w-md",
  md: "max-w-xl",
  lg: "max-w-2xl",
  xl: "max-w-3xl",
  "2xl": "max-w-4xl",
};

export function Dialog({ open, onOpenChange, size = "lg", children }: DialogProps) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onOpenChange(false);
      }}
    >
      <div className="bg-foreground/40 absolute inset-0" aria-hidden="true" />
      <div className={cn("relative z-10 w-full", SIZE_CLASS[size])}>{children}</div>
    </div>
  );
}

export const DialogContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "bg-background text-foreground rounded-lg border shadow-lg",
        "flex max-h-[85vh] flex-col overflow-hidden",
        className,
      )}
      {...props}
    />
  ),
);
DialogContent.displayName = "DialogContent";

export const DialogHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("flex flex-col gap-1.5 border-b px-6 py-5", className)}
      {...props}
    />
  ),
);
DialogHeader.displayName = "DialogHeader";

export const DialogTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h2 ref={ref} className={cn("text-lg font-semibold leading-none", className)} {...props} />
));
DialogTitle.displayName = "DialogTitle";

export const DialogDescription = React.forwardRef<
  HTMLParagraphElement,
  React.HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p ref={ref} className={cn("text-muted-foreground text-sm", className)} {...props} />
));
DialogDescription.displayName = "DialogDescription";

export const DialogBody = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("flex flex-col gap-4 overflow-y-auto px-6 py-5", className)}
      {...props}
    />
  ),
);
DialogBody.displayName = "DialogBody";

export const DialogFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("flex flex-row items-center justify-end gap-2 border-t px-6 py-4", className)}
      {...props}
    />
  ),
);
DialogFooter.displayName = "DialogFooter";
