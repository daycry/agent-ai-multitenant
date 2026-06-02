/**
 * Thin fetch wrapper for the api-server.
 *
 * Reads NEXT_PUBLIC_API_URL at build time. Tokens come from `lib/auth`
 * — never passed in by callers, so we only have one place to swap
 * localStorage for an httpOnly cookie later.
 */

import { getToken } from "@/lib/auth";
import { getTenantId } from "@/lib/tenant-storage";

// Default 8001 (not the more usual 8000) because a typical Windows
// dev box has something else parked on 8000. Override at build time
// with NEXT_PUBLIC_API_URL=... when needed.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

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

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { body, headers: extraHeaders, ...rest } = options;
  const headers = new Headers(extraHeaders);
  headers.set("Accept", "application/json");

  if (body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
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
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
