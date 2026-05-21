/**
 * Auth token storage.
 *
 * Phase 0 keeps the JWT in localStorage. Phase 15 will move it to an
 * httpOnly cookie set by the api-server. The single place that touches
 * the storage means the swap is local to this file.
 */

const TOKEN_KEY = "agentic.token";

export function setToken(token: string): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(TOKEN_KEY, token);
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(TOKEN_KEY);
  }
}
