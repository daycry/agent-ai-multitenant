import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Card primitive — refreshed for Plan 01 UI pass.
 *
 * - bg-card, rounded-xl, soft border, subtle shadow.
 * - Always: gentle hover lift + shadow bump.
 * - `interactive`: also gets an indigo border on hover (use for cards
 *   whose entire surface is clickable, e.g. template cards in the
 *   wizard).
 */
interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  interactive?: boolean;
}

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, interactive = false, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "bg-card text-card-foreground rounded-xl border shadow-sm",
        "transition-all duration-200",
        interactive
          ? "hover:border-primary/40 hover:shadow-[0_8px_28px_-12px_hsl(var(--primary)/0.35)] hover:-translate-y-0.5 cursor-pointer"
          : "hover:shadow-md",
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = "Card";

export const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("flex flex-col space-y-1.5 p-5", className)} {...props} />
  ),
);
CardHeader.displayName = "CardHeader";

export const CardTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn("text-base font-semibold leading-tight tracking-tight", className)}
    {...props}
  />
));
CardTitle.displayName = "CardTitle";

export const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("p-5 pt-0", className)} {...props} />
  ),
);
CardContent.displayName = "CardContent";
