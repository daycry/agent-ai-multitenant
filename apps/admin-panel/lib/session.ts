/**
 * Post-login tenant resolution by membership (ADR 0047, task_sso_03).
 *
 * After a successful login (password OR SSO) the issued session proves
 * IDENTITY only — it carries no active tenant. The client calls
 * `GET /auth/session/resolve` to turn the user's ACTIVE memberships into a
 * typed next step (no email-domain claiming, no auto-created membership):
 *
 *   - "no_access" → the user has NO tenant; show the "sin permisos,
 *     contacta al administrador" screen (`/admin/no-access`). The session
 *     stays valid; it just grants no tenant access.
 *   - "single"    → the backend already minted a TENANT-SCOPED token; we
 *     store it + the active tenant and enter the app directly.
 *   - "multiple"  → the tenant-picker (`/admin/select-tenant`) lets the
 *     user choose; that screen POSTs `/auth/session/select-tenant`.
 */

import { apiFetch } from "@/lib/api";
import { setToken } from "@/lib/auth";
import { setTenantId } from "@/lib/tenant-storage";

export type ResolutionState = "no_access" | "single" | "multiple";

export interface ResolvedMembership {
  tenant_id: string;
  tenant_name: string;
  role: string;
}

export interface SessionResolution {
  state: ResolutionState;
  memberships: ResolvedMembership[];
  access_token: string | null;
  token_type: string | null;
  expires_in: number | null;
}

// The no-access + tenant-select screens live OUTSIDE the `/admin` shell
// (like `/login`) because the user has no active tenant yet, so the
// sidebar/topbar that assume a tenant context must not render.
/** The route to land on for each resolution state. */
export const NO_ACCESS_ROUTE = "/no-access";
export const SELECT_TENANT_ROUTE = "/select-tenant";
export const HOME_ROUTE = "/admin/dashboard";

/** Call `GET /auth/session/resolve` with the current identity token. */
export async function resolveSession(): Promise<SessionResolution> {
  return apiFetch<SessionResolution>("/auth/session/resolve");
}

/**
 * Apply a `single`-state resolution: swap in the minted tenant-scoped
 * token and persist the only tenant as active. Used when a screen
 * re-resolves and finds exactly one membership.
 */
export function setTokenForSingle(resolution: SessionResolution): void {
  if (resolution.access_token) {
    setToken(resolution.access_token);
  }
  const only = resolution.memberships[0];
  if (only) {
    setTenantId(only.tenant_id);
  }
}

/**
 * Resolve the session and return the route the client should navigate to.
 *
 * Side effects for the `single` state: the minted tenant-scoped token
 * replaces the identity token and the active tenant is persisted, so the
 * app enters that tenant directly. For `multiple` we DON'T set a tenant
 * yet — the select screen does that after the user picks.
 */
export async function resolveAndRoute(): Promise<string> {
  const resolution = await resolveSession();

  if (resolution.state === "single") {
    // The backend minted a tenant-scoped token for the only membership.
    setTokenForSingle(resolution);
    return HOME_ROUTE;
  }

  if (resolution.state === "multiple") {
    return SELECT_TENANT_ROUTE;
  }

  // no_access: a valid identity with no tenant — clear any stale tenant.
  setTenantId(null);
  return NO_ACCESS_ROUTE;
}

/**
 * Activate one of the user's tenants (the picker's choice).
 *
 * POSTs the chosen tenant to `/auth/session/select-tenant`; the backend
 * re-asserts an active membership (a tenant the user doesn't belong to is
 * rejected) and mints a tenant-scoped token, which we store along with the
 * active tenant before entering the app.
 */
export async function selectTenant(tenantId: string): Promise<void> {
  const res = await apiFetch<{ access_token: string }>("/auth/session/select-tenant", {
    method: "POST",
    body: { tenant_id: tenantId },
  });
  setToken(res.access_token);
  setTenantId(tenantId);
}
