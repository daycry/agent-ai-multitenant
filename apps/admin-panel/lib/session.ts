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
 *   - "admin"     → a System Admin with NO membership; enter the PORTFOLIO
 *     view (no active tenant) with the tenant-less identity token already
 *     held. NEVER the no-access screen — the header picker switches tenant
 *     or bootstraps the first one.
 */

import { apiFetch } from "@/lib/api";
import { clearTenantId, setTenantId } from "@/lib/tenant-storage";

export type ResolutionState = "no_access" | "single" | "multiple" | "admin";

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
 * Apply a `single`-state resolution: persist the only tenant as active.
 *
 * It no longer stores a token (ADR 0133). `/auth/session/resolve` DOES mint a
 * tenant-scoped one for this state, but it delivers it by re-issuing the
 * httpOnly session cookie — the browser swaps the credential on its own and the
 * `access_token` in the body is there only for API clients. Renamed from
 * `setTokenForSingle` on purpose: a name that still said "set token" would send
 * the next reader looking for storage that must not exist.
 */
export function applySingleResolution(resolution: SessionResolution): void {
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
    // The backend minted a tenant-scoped session for the only membership and
    // already re-issued the cookie; we only record which tenant is active.
    applySingleResolution(resolution);
    return HOME_ROUTE;
  }

  if (resolution.state === "multiple") {
    return SELECT_TENANT_ROUTE;
  }

  if (resolution.state === "admin") {
    // System Admin with no membership: enter with the tenant-less identity
    // token already stored (no token minted). CLEAR the choice (unset, NOT
    // explicit "all") so TenantProvider auto-lands them in a real tenant and
    // tenant-scoped edits work; they can switch to "Todos los tenants" later.
    clearTenantId();
    return HOME_ROUTE;
  }

  // no_access: a valid identity with no tenant — clear any stale tenant.
  clearTenantId();
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
  await apiFetch<{ access_token: string }>("/auth/session/select-tenant", {
    method: "POST",
    body: { tenant_id: tenantId },
  });
  // The tenant-scoped session arrives as a re-issued cookie (ADR 0133); the
  // only thing left for the client is remembering which tenant is active.
  setTenantId(tenantId);
}
