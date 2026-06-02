"use client";

import * as React from "react";
import { Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/**
 * ListToolbar — the recurring "list header" row: an optional title +
 * count, an optional search box, and a slot of actions on the right.
 *
 * Presentation only. The search box is controlled (`search` +
 * `onSearchChange`) so the owning page keeps full control over the
 * filtering logic, query keys and `data-testid`s. Omit the search props
 * to render just the title + actions.
 *
 *   <ListToolbar
 *     title="Equipos"
 *     count={teams.length}
 *     search={q}
 *     onSearchChange={setQ}
 *     searchPlaceholder="Buscar equipos…"
 *     searchTestId="teams-search"
 *     actions={<Button>Nuevo</Button>}
 *   />
 */
interface ListToolbarProps {
  /** Left-hand heading (e.g. the section name or current filter). */
  title?: React.ReactNode;
  /** Optional count rendered next to the title in muted text. */
  count?: number;
  /** Controlled search value. Provide together with `onSearchChange`. */
  search?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  searchTestId?: string;
  /** ARIA label for the search field (defaults to the placeholder). */
  searchAriaLabel?: string;
  /** Right-hand action node(s): buttons, view-toggle, etc. */
  actions?: React.ReactNode;
  className?: string;
  "data-testid"?: string;
}

export function ListToolbar({
  title,
  count,
  search,
  onSearchChange,
  searchPlaceholder = "Buscar…",
  searchTestId,
  searchAriaLabel,
  actions,
  className,
  ...props
}: ListToolbarProps) {
  const hasSearch = typeof onSearchChange === "function";

  return (
    <div
      className={cn(
        "flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between",
        className,
      )}
      data-testid={props["data-testid"]}
    >
      {(title !== undefined || count !== undefined) && (
        <div className="flex min-w-0 items-baseline gap-2">
          {title !== undefined && (
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
          )}
          {count !== undefined && (
            <span className="text-muted-foreground text-sm tabular-nums">{count}</span>
          )}
        </div>
      )}

      <div className="flex flex-1 flex-wrap items-center justify-end gap-2 sm:flex-nowrap">
        {hasSearch && (
          <div className="relative w-full sm:max-w-xs">
            <Search
              className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              type="search"
              value={search ?? ""}
              onChange={(e) => onSearchChange?.(e.target.value)}
              placeholder={searchPlaceholder}
              aria-label={searchAriaLabel ?? searchPlaceholder}
              data-testid={searchTestId}
              className="pl-9"
            />
          </div>
        )}
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
