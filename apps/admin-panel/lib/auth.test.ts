// @vitest-environment jsdom
/**
 * The panel no longer holds the session token (ADR 0133, task_prod09_08).
 *
 * `lib/auth` used to be a `localStorage` wrapper around `agentic.token`; the
 * file itself carried a note saying "Phase 15 will move it to an httpOnly
 * cookie" since day one. It now knows only what a browser is *allowed* to know
 * about an httpOnly session: whether one exists (via the readable CSRF cookie
 * the server sets alongside) and what its double-submit token is.
 *
 * The first test is the one that matters: `getToken` must not come back. A
 * helper that reads the JWT is the whole vulnerability, and it is the kind of
 * thing that gets re-added "just for the upload endpoint".
 */

import { afterEach, describe, expect, it } from "vitest";

import * as auth from "@/lib/auth";
import { CSRF_COOKIE, SESSION_COOKIE, getCsrfToken, hasSession } from "@/lib/auth";

function setCookie(name: string, value: string): void {
  document.cookie = `${name}=${value}; path=/`;
}

function wipeCookies(): void {
  for (const chunk of document.cookie.split(";")) {
    const name = chunk.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; path=/; max-age=0`;
  }
}

afterEach(wipeCookies);

describe("the token accessors are gone", () => {
  it("exports no way to read the session JWT", () => {
    expect("getToken" in auth).toBe(false);
    expect("setToken" in auth).toBe(false);
    expect("clearToken" in auth).toBe(false);
  });
});

describe("hasSession", () => {
  it("is false with no cookies", () => {
    expect(hasSession()).toBe(false);
  });

  it("is true once the server has set the CSRF cookie", () => {
    setCookie(CSRF_COOKIE, "csrf-abc");
    expect(hasSession()).toBe(true);
  });

  it("is not fooled by a cookie whose name merely ENDS with the CSRF name", () => {
    // `x_agentic_csrf=...` must not read as `agentic_csrf=...`.
    setCookie(`x_${CSRF_COOKIE}`, "not-ours");
    expect(hasSession()).toBe(false);
    expect(getCsrfToken()).toBeNull();
  });
});

describe("getCsrfToken", () => {
  it("returns the double-submit token the panel must echo back", () => {
    setCookie(CSRF_COOKIE, "csrf-xyz");
    expect(getCsrfToken()).toBe("csrf-xyz");
  });

  it("url-decodes the value", () => {
    setCookie(CSRF_COOKIE, encodeURIComponent("a+b/c=="));
    expect(getCsrfToken()).toBe("a+b/c==");
  });

  it("finds the cookie when it is not the first one", () => {
    setCookie("other", "1");
    setCookie(CSRF_COOKIE, "csrf-2");
    setCookie("another", "2");
    expect(getCsrfToken()).toBe("csrf-2");
  });
});

describe("clearClientSession", () => {
  it("drops the readable half so the panel stops believing it is logged in", () => {
    setCookie(CSRF_COOKIE, "csrf-abc");
    auth.clearClientSession();
    expect(hasSession()).toBe(false);
  });
});

describe("SESSION_COOKIE", () => {
  it("names the httpOnly cookie the Next middleware gates on", () => {
    // Not readable from JS by design — this constant exists so the edge
    // middleware and the api-server agree on ONE name.
    expect(SESSION_COOKIE).toBe("agentic_session");
    expect(CSRF_COOKIE).toBe("agentic_csrf");
  });
});
