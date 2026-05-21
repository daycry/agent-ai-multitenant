/**
 * Tiny localStorage wrapper for the active-tenant choice.
 *
 * Why not pull from React context inside `apiFetch`? Because
 * `apiFetch` is called from query functions and mutations outside
 * the React tree (the TanStack Query worker). Keeping the source of
 * truth in localStorage means *anyone* — fetch helpers, mutations,
 * SSR-ish module code — can read it without prop-drilling.
 *
 * `getTenantId()` returns null when no tenant has been picked (which
 * means the "All tenants" / portfolio view for a superadmin); the
 * fetch wrapper just omits the `X-Tenant-Id` header in that case.
 */

const STORAGE_KEY = "admin-panel.tenant-id";

export function getTenantId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(STORAGE_KEY);
}

export function setTenantId(tenantId: string | null): void {
  if (typeof window === "undefined") return;
  if (tenantId) {
    window.localStorage.setItem(STORAGE_KEY, tenantId);
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }
}
