/**
 * Seeding a panel session for the e2e suite (ADR 0133, condición 1).
 *
 * Ninety-odd specs used to open with the same three lines:
 *
 *     await page.addInitScript(() => {
 *       window.localStorage.setItem("agentic.token", "e2e-fake-token");
 *     });
 *
 * — a deliberate shortcut documented in ADR 0015, because the admin layout's
 * auth gate only ever checked that key on the client. Moving the session to an
 * httpOnly cookie invalidates all of them at once: the gate is now
 * `middleware.ts`, which runs on the edge and reads a cookie the page cannot
 * write.
 *
 * The ADR made the migration route a CONDITION of accepting option A: with a
 * helper it is one mechanical substitution; spec by spec it is 16-24 h of
 * hand-editing and the recommendation flips to option B. So this file exists
 * precisely so the next change of session shape is one edit, not ninety.
 *
 * What it seeds:
 *
 *   * `agentic_session` — the httpOnly session cookie the Next middleware
 *     gates on. Its VALUE is never inspected in the mocked suite (every API
 *     call is intercepted by `page.route`), only its presence.
 *   * `agentic_csrf`    — the readable double-submit token `lib/api` echoes in
 *     `X-CSRF-Token` on mutations. Without it, a spec that exercises a POST
 *     would send no CSRF header, which is a real-backend 403 and, worse, would
 *     let a regression in `apiFetch` pass unnoticed.
 *   * optionally the active tenant in `localStorage`, which is NOT a
 *     credential and legitimately stays there.
 */

import type { Page } from "@playwright/test";

/** Session cookie name — must match `lib/auth.ts` / `api_server.auth.cookies`. */
export const SESSION_COOKIE = "agentic_session";
/** Readable double-submit cookie name. */
export const CSRF_COOKIE = "agentic_csrf";
/** localStorage key holding the active tenant (not a credential). */
export const TENANT_STORAGE_KEY = "admin-panel.tenant-id";

/** The fake JWT the mocked suite has always used. Kept identical so a spec
 *  that asserts on it (or on a mocked Authorization header) keeps working. */
export const E2E_SESSION_TOKEN = "e2e-fake-token";
/** The fake CSRF token. Any value works — the point is that it round-trips. */
export const E2E_CSRF_TOKEN = "e2e-csrf-token";

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export interface SeedSessionOptions {
  /** Override the session token (specs that assert on a specific value). */
  token?: string;
  /** Persist an active tenant, the way the old `addInitScript` pair did. */
  tenantId?: string;
  /** Override the CSRF token. */
  csrfToken?: string;
}

/**
 * Give `page`'s browser context an authenticated session.
 *
 * Cookies go on the CONTEXT, not the page, so they survive every navigation
 * and every new tab the spec opens — the same lifetime `addInitScript` gave the
 * old localStorage seed.
 *
 * `secure` follows the base URL: Chromium accepts `Secure` cookies over
 * `http://localhost` (a trusted origin) but not over any other plain-http host,
 * so a suite pointed at `http://some-box:3000` would silently get NO cookie and
 * fail with a redirect to `/login` that looks like an app bug.
 */
export async function seedSession(page: Page, options: SeedSessionOptions = {}): Promise<void> {
  const url = new URL(BASE_URL);
  const secure = url.protocol === "https:";
  await page.context().addCookies([
    {
      name: SESSION_COOKIE,
      value: options.token ?? E2E_SESSION_TOKEN,
      domain: url.hostname,
      path: "/",
      httpOnly: true,
      secure,
      sameSite: "Lax",
    },
    {
      name: CSRF_COOKIE,
      value: options.csrfToken ?? E2E_CSRF_TOKEN,
      domain: url.hostname,
      path: "/",
      httpOnly: false,
      secure,
      sameSite: "Lax",
    },
  ]);

  if (options.tenantId) {
    await page.addInitScript(
      ([key, id]) => {
        window.localStorage.setItem(key, id);
      },
      [TENANT_STORAGE_KEY, options.tenantId],
    );
  }
}

/** Drop the session — for specs that assert the logged-out behaviour. */
export async function clearSession(page: Page): Promise<void> {
  await page.context().clearCookies();
}

/**
 * The session JWT, read from the browser context.
 *
 * ONLY for specs that talk to a REAL api-server through `page.request` and need
 * an `Authorization` header (`page.request` shares the context's cookie jar, so
 * most of them do not). Playwright can read httpOnly cookies; the browser
 * cannot, which is the entire point of the migration — so if you find yourself
 * reaching for this from page-side code, that is the bug.
 */
export async function sessionToken(page: Page): Promise<string | null> {
  const cookies = await page.context().cookies();
  return cookies.find((c) => c.name === SESSION_COOKIE)?.value ?? null;
}
