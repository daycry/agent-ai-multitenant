/**
 * Thin fetch wrapper for the api-server.
 *
 * Reads NEXT_PUBLIC_API_URL at build time.
 *
 * SESSION (ADR 0133, task_prod09_08): the credential is the `agentic_session`
 * httpOnly cookie, sent by `credentials: "include"`. There is no
 * `Authorization` header any more and no token to read — that was the point of
 * the migration. The price of cookies is CSRF, paid by echoing the readable
 * `agentic_csrf` cookie in `X-CSRF-Token` on every state-changing method; the
 * api-server rejects a cookie-authenticated mutation without it (403).
 *
 * 401 (task_prod09_10, frontend-3): handled ONCE, here. Every screen used to
 * render the raw body of a 401 and leave the user parked on a dead page with no
 * way back; now an expired/revoked session drops the local state and hands the
 * route to `setUnauthorizedHandler`, which the app wires to
 * `/login?next=<route>`.
 */

import { CSRF_HEADER, clearClientSession, getCsrfToken } from "@/lib/auth";
import { clearTenantId, getTenantId } from "@/lib/tenant-storage";

// Default 8001 (not the more usual 8000) because a typical Windows
// dev box has something else parked on 8000. Override at build time
// with NEXT_PUBLIC_API_URL=... when needed.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

/** Methods that change state and therefore need the CSRF proof. Anything not
 *  listed as safe is treated as unsafe — a new verb defaults to PROTECTED. */
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

/**
 * Paths whose 401 is a NORMAL answer, not an expired session.
 *
 * A wrong password answers 401 from `/auth/login`; bouncing to `/login` there
 * is a reload that throws away the error message the user needs to read.
 */
const NO_REDIRECT_ON_401 = ["/auth/login", "/auth/mfa/"];

/**
 * Absolute URL on the api-server for a relative `path`.
 *
 * `apiFetch` is for JSON round-trips; this helper is for the cases where
 * the BROWSER itself must navigate to the api-server — e.g. the SSO login
 * routes (`/auth/sso/{id}/oidc|saml/login`) reply with a 307/302 redirect
 * to the IdP, so the login button does a full-page navigation here rather
 * than an XHR. `path` is expected to be server-relative (leading `/`).
 */
export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(status: number, body: string) {
    super(`api ${status}: ${body}`);
    this.status = status;
    this.body = body;
  }
}

export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

/** Called once per 401, with the route the user was on. */
export type UnauthorizedHandler = (next: string) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

/**
 * Register what happens when the session dies (see `app/providers.tsx`).
 *
 * Injected rather than imported so this module stays free of `next/navigation`
 * — it runs inside TanStack Query workers and in tests, outside any React tree.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

/** The route to come back to after re-login. */
function currentRoute(): string {
  if (typeof window === "undefined") return "/";
  return `${window.location.pathname}${window.location.search}`;
}

function handleUnauthorized(path: string): void {
  if (NO_REDIRECT_ON_401.some((prefix) => path.startsWith(prefix))) return;
  // Drop BOTH: a surviving tenant choice would be re-sent as the acting
  // tenant of whoever logs in next in the same tab.
  clearClientSession();
  clearTenantId();
  unauthorizedHandler?.(currentRoute());
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { body, headers: extraHeaders, ...rest } = options;
  const headers = new Headers(extraHeaders);
  headers.set("Accept", "application/json");

  if (body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const method = (rest.method ?? "GET").toUpperCase();
  if (!SAFE_METHODS.has(method) && !headers.has(CSRF_HEADER)) {
    const csrf = getCsrfToken();
    if (csrf) headers.set(CSRF_HEADER, csrf);
  }

  // For superadmins the backend honors this header as the acting
  // tenant; for non-admins it's silently ignored, so we can always
  // send it without leaking scope. Empty value (= "all tenants"
  // portfolio view) just doesn't send the header.
  const tenantId = getTenantId();
  if (tenantId && !headers.has("X-Tenant-Id")) {
    headers.set("X-Tenant-Id", tenantId);
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...rest,
    headers,
    // The session cookie is cross-ORIGIN in dev (panel :3000 → api :8001) and
    // same-origin in production behind Caddy. Without this it travels in
    // neither case and every request is anonymous.
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    if (response.status === 401) handleUnauthorized(path);
    throw new ApiError(response.status, text);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
