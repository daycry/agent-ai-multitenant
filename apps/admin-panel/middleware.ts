/**
 * Edge gate for the authenticated routes (ADR 0133, task_prod09_08).
 *
 * The panel's session is an httpOnly cookie now, which is what makes this file
 * possible at all: the previous gate was a `useEffect` inside
 * `app/admin/layout.tsx` that could only run AFTER the protected page had been
 * rendered, shipped and hydrated — a flash of protected UI, and unavoidable
 * while the credential lived in `localStorage` (the edge cannot read it).
 *
 * Scope, deliberately: this checks that a session cookie is PRESENT. It does
 * not decode it, does not verify the signature and does not ask Redis whether
 * it is still alive — the edge has no JWT secret and no Redis, and pretending
 * otherwise would put a second, weaker copy of the authentication rules in the
 * one place that cannot enforce them. Authorisation stays where it belongs: the
 * api-server 401s a dead session and `apiFetch` turns that into a redirect
 * (task_prod09_10). This gate exists so an unauthenticated visitor never gets
 * the page in the first place.
 */

import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/auth";

/**
 * Routes that require a session.
 *
 * `/login` is NOT here on purpose — matching it would redirect the login page
 * to itself. `/select-tenant` and `/no-access` are: they live outside the
 * `/admin` shell (the user has no tenant yet) but they are still post-login
 * screens and used to be guarded by their own client-side `getToken()` checks.
 */
export const config = {
  matcher: ["/admin/:path*", "/select-tenant", "/no-access"],
};

export function middleware(request: NextRequest): NextResponse {
  if (request.cookies.get(SESSION_COOKIE)) {
    return NextResponse.next();
  }

  const target = request.nextUrl.clone();
  const wanted = `${request.nextUrl.pathname}${request.nextUrl.search}`;
  target.pathname = "/login";
  target.search = "";
  // Carry the requested route so login can put the user back where they were
  // instead of dumping everyone on the dashboard (task_prod09_10).
  target.searchParams.set("next", wanted);
  return NextResponse.redirect(target);
}
