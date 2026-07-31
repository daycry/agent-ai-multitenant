/**
 * Purging the TanStack cache when the identity changes (task_prod09_11,
 * frontend-4).
 *
 * The QueryClient lives in the ROOT layout, which does not unmount on logout —
 * so after "cerrar sesión" and a login as somebody else, in the same tab, the
 * previous user's data was still sitting in the cache and got painted while the
 * refetch was in flight (`staleTime: 30 s`, and 5 min for `/me`).
 * `queryClient.clear()` is the fix, and it has to happen where the client is
 * reachable.
 *
 * The logout handlers live in components that are NOT inside the provider tree
 * they would need (`app/no-access`) or would have to thread a prop through the
 * whole header, so the client is registered here once by `app/providers.tsx`
 * and read back by whoever needs it. A module-level reference rather than a
 * context on purpose: this is used from event handlers and from `lib/api`'s 401
 * path, both outside React's render cycle.
 */

import type { QueryClient } from "@tanstack/react-query";

let client: QueryClient | null = null;

/** Called by `app/providers.tsx` on mount (and with `null` on unmount). */
export function setSessionQueryClient(next: QueryClient | null): void {
  client = next;
}

/**
 * Drop EVERYTHING cached. For logout and for a 401.
 *
 * `clear()` and not `invalidateQueries()`: invalidation marks data stale but
 * keeps serving it until the refetch lands, which is precisely the window in
 * which the outgoing user's data is shown to the incoming one.
 */
export function purgeSessionCache(): void {
  client?.clear();
}

/**
 * Drop the TENANT-scoped queries after switching tenant.
 *
 * `resetQueries` (not `invalidateQueries`) because the mounted screens must go
 * back to their loading state instead of re-rendering the OUTGOING tenant's
 * rows while the new fetch is in flight — the tenant picker's whole job is that
 * you never see another tenant's data.
 *
 * `auth` / `admin` / `system-health` are exempt: the user identity and the
 * platform-wide (BYPASSRLS) reads do not depend on the active tenant, and
 * wiping them on every switch forces needless refetches of data that did not
 * change.
 */
const TENANT_INDEPENDENT_KEYS = new Set(["auth", "admin", "system-health"]);

export function resetTenantScopedQueries(target: QueryClient): void {
  target.resetQueries({
    predicate: (query) =>
      !query.queryKey.some((key) => typeof key === "string" && TENANT_INDEPENDENT_KEYS.has(key)),
  });
}
