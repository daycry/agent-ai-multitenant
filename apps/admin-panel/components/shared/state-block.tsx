import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

/**
 * StateBlock — one consistent way to render the loading / error / empty
 * triad that almost every list & detail page repeats by hand.
 *
 * It is a pure presentation helper: callers keep owning their queries,
 * routing and `data-testid`s. Pass the flags + content you already have
 * and StateBlock picks the right visual:
 *
 *   <StateBlock
 *     isLoading={q.isLoading}
 *     isError={q.isError}
 *     error={q.error}
 *     isEmpty={(q.data ?? []).length === 0}
 *     loadingTestId="teams-loading"
 *     errorTestId="teams-error"
 *     emptyTestId="teams-empty"
 *     emptyTitle="No hay equipos"
 *   >
 *     {/* rows / grid go here, rendered only when there's data *\/}
 *   </StateBlock>
 *
 * Precedence: loading → error → empty → children. When none of the
 * flags is set it renders `children` untouched, so it is safe to wrap a
 * whole list region.
 */

/** Pull a human string out of whatever a query throws. */
function errorMessage(error: unknown): string {
  if (!error) return "Error desconocido.";
  // ApiError (and most Error subclasses) expose `.body` / `.message`;
  // read them structurally to avoid importing the type here.
  const e = error as { body?: unknown; message?: unknown };
  if (typeof e.body === "string" && e.body.length > 0) return e.body;
  if (typeof e.message === "string" && e.message.length > 0) return e.message;
  return String(error);
}

interface StateBlockProps {
  isLoading?: boolean;
  isError?: boolean;
  /** Error object/string; used to render a message when `isError`. */
  error?: unknown;
  /** Render the empty placeholder instead of children. */
  isEmpty?: boolean;

  /** Loading copy + skeleton control. */
  loadingLabel?: React.ReactNode;
  /** When true, show pulsing skeleton rows instead of a spinner line. */
  loadingSkeleton?: boolean;
  /** Number of skeleton rows when `loadingSkeleton`. */
  skeletonRows?: number;

  /** Empty placeholder content (forwarded to EmptyState). */
  emptyIcon?: LucideIcon;
  emptyTitle?: React.ReactNode;
  emptyDescription?: React.ReactNode;
  emptyAction?: React.ReactNode;
  /** Drop-in replacement for the whole empty block (overrides the above). */
  empty?: React.ReactNode;

  /** Error heading; the message is derived from `error`. */
  errorTitle?: React.ReactNode;

  loadingTestId?: string;
  errorTestId?: string;
  emptyTestId?: string;

  className?: string;
  children?: React.ReactNode;
}

export function StateBlock({
  isLoading,
  isError,
  error,
  isEmpty,
  loadingLabel = "Cargando…",
  loadingSkeleton = false,
  skeletonRows = 3,
  emptyIcon,
  emptyTitle = "Sin resultados",
  emptyDescription,
  emptyAction,
  empty,
  errorTitle = "No se pudo cargar",
  loadingTestId,
  errorTestId,
  emptyTestId,
  className,
  children,
}: StateBlockProps) {
  if (isLoading) {
    if (loadingSkeleton) {
      return (
        <div
          className={cn("space-y-2", className)}
          data-testid={loadingTestId}
          aria-busy="true"
          aria-live="polite"
        >
          {Array.from({ length: skeletonRows }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      );
    }
    return (
      <p
        className={cn("text-muted-foreground flex items-center gap-2 text-sm", className)}
        data-testid={loadingTestId}
        aria-busy="true"
        aria-live="polite"
      >
        <Spinner />
        {loadingLabel}
      </p>
    );
  }

  if (isError) {
    return (
      <Card className={cn("p-4", className)} data-testid={errorTestId} role="alert">
        {errorTitle ? <p className="text-foreground text-sm font-semibold">{errorTitle}</p> : null}
        <p className="text-danger-soft-foreground text-sm">{errorMessage(error)}</p>
      </Card>
    );
  }

  if (isEmpty) {
    if (empty !== undefined) {
      return (
        <div className={className} data-testid={emptyTestId}>
          {empty}
        </div>
      );
    }
    return (
      <EmptyState
        className={className}
        data-testid={emptyTestId}
        icon={emptyIcon}
        title={emptyTitle}
        description={emptyDescription}
        action={emptyAction}
      />
    );
  }

  return <>{children}</>;
}
