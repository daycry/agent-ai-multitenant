"use client";

/**
 * Top-of-page progress bar that activates whenever ANY TanStack
 * Query or Mutation is in flight.
 *
 * Why a global bar instead of per-component spinners only? When the
 * user clicks "Crear proyecto" / "Añadir miembro" / etc., the
 * action involves a POST + a couple of invalidated queries that
 * re-fetch as the panel hops to the next screen. Without a global
 * indicator the user sees the button briefly disable and then a
 * blank panel for 1-2 seconds — looks like the click did nothing.
 *
 * Implementation:
 *   - `useIsFetching()` counts active query fetches (initial + refetch).
 *   - `useIsMutating()` counts active mutations (POST/PUT/DELETE).
 *   - When either is > 0, the bar is visible with a pulsing
 *     gradient. Disappears with a short fade once both reach 0.
 *
 * Rendered inside `AdminShell` so it sits at the very top of the
 * main column, just under the sticky header. Login page is outside
 * the shell and uses the in-button Spinner instead.
 */

import { useIsFetching, useIsMutating } from "@tanstack/react-query";

import { cn } from "@/lib/utils";

export function GlobalProgress() {
  const fetching = useIsFetching();
  const mutating = useIsMutating();
  const active = fetching > 0 || mutating > 0;

  return (
    <div
      aria-hidden="true"
      data-testid="global-progress"
      data-active={active ? "true" : "false"}
      className={cn(
        "pointer-events-none fixed inset-x-0 top-0 z-50 h-1 overflow-hidden",
        "transition-opacity duration-200",
        active ? "opacity-100" : "opacity-0",
      )}
    >
      {/* Solid base in the brand gradient so the bar is always
          visible while active. */}
      <div className="bg-brand-gradient h-full w-full" />
      {/* Bright shimmer sliding across the base. The keyframe lives
          in globals.css (`@keyframes progress-stripe`). */}
      {active && (
        <div
          className={cn(
            "animate-progress-stripe absolute inset-0",
            "bg-gradient-to-r from-transparent via-white/80 to-transparent",
          )}
        />
      )}
    </div>
  );
}
