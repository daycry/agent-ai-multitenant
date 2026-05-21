import * as React from "react";

import { cn } from "@/lib/utils";

interface PageHeaderProps {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  "data-testid"?: string;
}

/**
 * Hero header for admin pages.
 *
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  [icon │ gradient bg]  Title (h1, big)        [actions]    │
 *   │                        Subtitle / description              │
 *   └────────────────────────────────────────────────────────────┘
 *
 * - Icon block has the indigo→violet brand gradient + soft glow.
 * - Title is gradient text when no icon is provided (rare).
 * - Stacks vertically on mobile; actions go below title block.
 */
export function PageHeader({
  icon,
  title,
  description,
  actions,
  className,
  ...props
}: PageHeaderProps) {
  return (
    <header
      className={cn(
        "mb-8 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between",
        className,
      )}
      data-testid={props["data-testid"] ?? "page-header"}
    >
      <div className="flex items-center gap-3 sm:gap-4">
        {icon && (
          <div
            className={cn(
              "bg-brand-gradient flex h-11 w-11 shrink-0 items-center justify-center",
              "rounded-xl text-white shadow-[0_10px_30px_-12px_hsl(var(--gradient-from)/0.55)]",
              "sm:h-14 sm:w-14",
            )}
            aria-hidden="true"
          >
            {icon}
          </div>
        )}
        <div className="flex min-w-0 flex-col gap-1">
          <h1
            className="text-2xl font-semibold tracking-tight sm:text-3xl"
            data-testid="page-title"
          >
            {title}
          </h1>
          {description && (
            <p className="text-muted-foreground text-sm sm:text-[15px]">{description}</p>
          )}
        </div>
      </div>
      {actions && (
        <div className="flex flex-row flex-wrap items-center gap-2 sm:shrink-0">{actions}</div>
      )}
    </header>
  );
}
