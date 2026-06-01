import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * EmptyState — a centered, consistent placeholder for "no data yet",
 * empty searches, or first-run screens.
 *
 * - `icon`: optional lucide icon component, shown in a soft tinted disc.
 * - `title`: short headline (required).
 * - `description`: optional supporting line.
 * - `action`: optional CTA node (e.g. a Button) rendered below.
 * - children: any extra content under the action.
 *
 * Presentation only; callers keep owning behaviour + `data-testid`.
 */
interface EmptyStateProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  icon?: LucideIcon;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
}

export const EmptyState = React.forwardRef<HTMLDivElement, EmptyStateProps>(
  ({ className, icon: Icon, title, description, action, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center",
        className,
      )}
      {...props}
    >
      {Icon ? (
        <span
          className="bg-muted text-muted-foreground flex h-12 w-12 items-center justify-center rounded-full"
          aria-hidden="true"
        >
          <Icon className="h-6 w-6" />
        </span>
      ) : null}
      <div className="space-y-1">
        <p className="text-foreground text-sm font-semibold">{title}</p>
        {description ? (
          <p className="text-muted-foreground mx-auto max-w-sm text-sm">{description}</p>
        ) : null}
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
      {children}
    </div>
  ),
);
EmptyState.displayName = "EmptyState";
