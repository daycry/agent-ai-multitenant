/**
 * Tiny localStorage wrapper for the active-tenant choice.
 *
 * Why not pull from React context inside `apiFetch`? Because
 * `apiFetch` is called from query functions and mutations outside
 * the React tree (the TanStack Query worker). Keeping the source of
 * truth in localStorage means *anyone* — fetch helpers, mutations,
 * SSR-ish module code — can read it without prop-drilling.
 *
 * THREE states, not two — this is what lets a fresh superadmin work
 * immediately without clobbering a deliberate portfolio choice:
 *   - a tenant UUID         → that tenant is active (X-Tenant-Id sent).
 *   - the `__all__` sentinel → the operator EXPLICITLY chose "Todos los
 *     tenants" (portfolio); sticky, never auto-overridden.
 *   - absent (null)          → never chosen. A superadmin is auto-landed
 *     in a tenant by `TenantProvider` so tenant-scoped edits work; a
 *     regular user just has no active tenant.
 *
 * `getTenantId()` (the X-Tenant-Id value) is null for BOTH the sentinel
 * and the unset case, so the fetch wrapper omits the header in either —
 * the distinction only matters to the provider's auto-default logic.
 */

const STORAGE_KEY = "admin-panel.tenant-id";

/** Explicit "Todos los tenants" (portfolio) marker — distinct from unset. */
const ALL_TENANTS = "__all__";

/** Raw stored choice: a tenant UUID, the `__all__` sentinel, or null when unset. */
export function getTenantChoice(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(STORAGE_KEY);
}

/** The X-Tenant-Id value to send: a UUID, or null for "all"/unset (omit header). */
export function getTenantId(): string | null {
  const choice = getTenantChoice();
  return choice && choice !== ALL_TENANTS ? choice : null;
}

/** Persist a specific active tenant (UUID). */
export function setTenantId(tenantId: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, tenantId);
}

/** Persist the EXPLICIT "Todos los tenants" portfolio choice (sticky). */
export function setAllTenants(): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, ALL_TENANTS);
}

/** Clear the choice entirely (unset) — e.g. on logout or post-login reset. */
export function clearTenantId(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
}
