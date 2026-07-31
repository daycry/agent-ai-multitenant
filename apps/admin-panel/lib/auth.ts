/**
 * What the panel is allowed to know about its own session (ADR 0133).
 *
 * This file used to be a `localStorage` wrapper around the raw JWT, with a
 * comment promising "Phase 15 will move it to an httpOnly cookie" that stayed
 * there for the whole life of the project. The session now lives in
 * `agentic_session`, an `httpOnly + Secure + SameSite=Lax` cookie the api-server
 * sets at login — so JavaScript, ours or an attacker's, cannot read the
 * credential of a System Admin whose token is cross-tenant.
 *
 * There is deliberately NO `getToken()` here any more. Its absence is the
 * feature: `lib/api` sends the session by `credentials: "include"` and the
 * WebSocket gets it in the handshake, so nothing in the panel needs the token
 * value. `lib/auth.test.ts` asserts the accessor stays gone.
 *
 * What IS readable is `agentic_csrf`, the double-submit half. Cookies travel
 * automatically, which is what creates a CSRF surface the Bearer scheme never
 * had; echoing this value in `X-CSRF-Token` proves the request came from our
 * own origin. Because the server sets and clears both cookies together, the
 * presence of the readable one is also the panel's honest answer to "is there a
 * session?".
 */

/** The httpOnly session cookie. Not readable here — named so the Next edge
 *  middleware (`middleware.ts`) and the api-server agree on one spelling. */
export const SESSION_COOKIE = "agentic_session";

/** The readable double-submit token cookie. */
export const CSRF_COOKIE = "agentic_csrf";

/** Header the panel echoes the CSRF token back in. */
export const CSRF_HEADER = "X-CSRF-Token";

/**
 * Read one cookie by exact name.
 *
 * Split-then-compare rather than a regex over the whole cookie string: a
 * substring/`endsWith` match would let `x_agentic_csrf` answer for
 * `agentic_csrf`, and a value an attacker can plant under a name of their
 * choosing is precisely what must not be trusted here.
 */
export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  for (const chunk of document.cookie.split(";")) {
    const eq = chunk.indexOf("=");
    if (eq < 0) continue;
    if (chunk.slice(0, eq).trim() !== name) continue;
    const raw = chunk.slice(eq + 1).trim();
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  }
  return null;
}

/** The double-submit token to send with every mutation, or null. */
export function getCsrfToken(): string | null {
  return readCookie(CSRF_COOKIE);
}

/**
 * Whether the browser is holding a session.
 *
 * Answered from the READABLE half — the session cookie is httpOnly by design.
 * That makes this a hint, not a guarantee: the authoritative gates are the Next
 * middleware (server-side, sees the real cookie) and the api-server's 401.
 * Use it for "should I even try", never as authorisation.
 */
export function hasSession(): boolean {
  return getCsrfToken() !== null;
}

/**
 * Forget the session client-side.
 *
 * Only the readable cookie can be expired from here; `POST /auth/logout` is
 * what revokes the server-side session and clears the httpOnly one. This exists
 * for the path where the API is unreachable or already answered 401 — without
 * it the panel would keep believing it is logged in and bounce the user around
 * a redirect loop.
 */
export function clearClientSession(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${CSRF_COOKIE}=; path=/; max-age=0`;
}
