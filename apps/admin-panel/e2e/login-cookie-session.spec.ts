import { expect, test } from "@playwright/test";
import { clearSession, seedSession, SESSION_COOKIE } from "./helpers/session";

/**
 * The panel session is a cookie, not a localStorage key (ADR 0133,
 * task_prod09_08 — `auto_prod09_08_b`).
 *
 * Two properties, both of which the old `localStorage` gate could not have:
 *
 *   1. WITHOUT a session, `/admin/*` is redirected to `/login` by
 *      `middleware.ts` — on the EDGE, before the protected page is generated.
 *      The previous gate was a `useEffect`, so the page was rendered, shipped
 *      and hydrated first: a flash of protected UI.
 *   2. WITH a session, nothing in `localStorage` looks like a JWT. That is ADR
 *      verification item 1, and it is the whole point of the migration: an XSS
 *      can no longer read a System Admin's cross-tenant credential.
 *
 * NOTE (honesty): written as part of the ADR 0133 delivery but NOT executed —
 * this environment has no Playwright browser. It is coherent with the helper
 * and with `middleware.ts`, and it is the first thing to run when a browser is
 * available.
 */

test("a session-less visit to /admin is redirected to /login before the page renders", async ({
  page,
}) => {
  await clearSession(page);

  await page.goto("/admin/dashboard");

  await expect(page).toHaveURL(/\/login/);
  // The requested route is carried so login can put the user back.
  expect(new URL(page.url()).searchParams.get("next")).toBe("/admin/dashboard");
});

test("with a session cookie, nothing that looks like a JWT is left in localStorage", async ({
  page,
}) => {
  await seedSession(page);
  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "00000000-0000-0000-0000-000000000001",
        email: "root@example.com",
        full_name: "Root",
        is_system_admin: true,
        is_system_owner: true,
        is_active: true,
        active_tenant_id: null,
        memberships: [],
      }),
    }),
  );

  await page.goto("/admin/dashboard");
  await expect(page).toHaveURL(/\/admin\/dashboard/);

  const stored = await page.evaluate(() =>
    Object.entries(window.localStorage).map(([k, v]) => `${k}=${v}`),
  );
  // No key holds a JWT-shaped value, and the old key is gone for good.
  expect(stored.some((entry) => entry.includes("agentic.token"))).toBe(false);
  expect(stored.some((entry) => /ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(entry))).toBe(false);

  // The credential IS there — in a cookie the page cannot read.
  const cookies = await page.context().cookies();
  const session = cookies.find((c) => c.name === SESSION_COOKIE);
  expect(session).toBeTruthy();
  expect(session?.httpOnly).toBe(true);
});
