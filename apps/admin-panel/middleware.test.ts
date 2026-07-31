/**
 * Server-side gate for the authenticated routes (ADR 0133, task_prod09_08).
 *
 * Until now `/admin/*` was protected by a `useEffect` in the layout: the server
 * rendered the protected page, shipped it, hydrated it, and only THEN did the
 * client decide to redirect. A gate that runs after the page is on screen is a
 * flash of protected UI, not a gate — and it was the only option available,
 * because the Next edge cannot read `localStorage`.
 *
 * With the session in a cookie the edge CAN read it, so the redirect happens
 * before a byte of the page is generated. That is ADR verification item 4.
 */

import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import { middleware, config } from "@/middleware";
import { SESSION_COOKIE } from "@/lib/auth";

function request(path: string, opts: { session?: boolean } = {}): NextRequest {
  const req = new NextRequest(new URL(`http://localhost:3000${path}`));
  if (opts.session) req.cookies.set(SESSION_COOKIE, "a.jwt.value");
  return req;
}

describe("without a session cookie", () => {
  it("redirects /admin/* to /login", () => {
    const response = middleware(request("/admin/dashboard"));
    const location = response.headers.get("location");
    expect(response.status).toBe(307);
    expect(new URL(location!).pathname).toBe("/login");
  });

  it("preserves the route the user asked for, query string included", () => {
    const response = middleware(request("/admin/plans/abc?tab=tasks"));
    const location = new URL(response.headers.get("location")!);
    expect(location.searchParams.get("next")).toBe("/admin/plans/abc?tab=tasks");
  });

  it("also gates the post-login screens", () => {
    for (const path of ["/select-tenant", "/no-access"]) {
      const location = middleware(request(path)).headers.get("location");
      expect(new URL(location!).pathname, path).toBe("/login");
    }
  });
});

describe("with a session cookie", () => {
  it("lets the request through", () => {
    const response = middleware(request("/admin/dashboard", { session: true }));
    // `NextResponse.next()` carries no Location — the request continues.
    expect(response.headers.get("location")).toBeNull();
  });
});

describe("matcher", () => {
  it("covers the authenticated routes and NOT the login page", () => {
    // A matcher that included `/login` would redirect the login page to
    // itself forever; one that missed `/admin` would leave the hole open.
    expect(config.matcher).toContain("/admin/:path*");
    expect(config.matcher).toContain("/select-tenant");
    expect(config.matcher).toContain("/no-access");
    expect(config.matcher.some((m) => m.startsWith("/login"))).toBe(false);
  });
});
