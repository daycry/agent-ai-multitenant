import { expect, test, type Page } from "@playwright/test";

/**
 * Plan 10 task_10_16 — in-app notification inbox (history + read/unread + retry).
 *
 * PENDING HUMAN VERIFICATION: written, NOT run in this environment (no
 * browser / node-playwright runtime here). A human runs this once the dev
 * stack + admin-panel are up.
 *
 * Pre-conditions (caller's responsibility):
 *   - docker compose stack is up; api-server on http://localhost:8001 with
 *     CORS allowing http://localhost:3000; admin-panel dev server on :3000.
 *   - A Tenant Admin user exists; a `tenant_user` (member) exists in the same
 *     tenant for the per-user-marker case.
 *   - The tenant has some notification_logs history, including at least one
 *     dead-lettered send so the manual-retry link is exercised. Seed via the
 *     dispatcher (Phase A-C) or directly.
 *
 * Backend constraints this UI honours (enforced + tested server-side in
 * tests/integration/test_notification_inbox.py):
 *   - the inbox is tenant+user-scoped (RLS) — never another tenant's logs;
 *   - read/unread is per-user + idempotent;
 *   - the manual retry reuses POST /notifications/logs/{id}/retry (task_10_13),
 *     which is tenant_admin only;
 *   - pagination is bounded (limit 1..200, offset >= 0).
 */

const ADMIN_EMAIL = process.env.E2E_TENANT_ADMIN_EMAIL ?? "admin@tenant.example.com";
const ADMIN_PASSWORD = process.env.E2E_TENANT_ADMIN_PASSWORD ?? "longenoughpw";

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\//);
}

test("inbox lists the tenant's notification history", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/notifications/inbox");
  await expect(page.getByTestId("notification-inbox-page")).toBeVisible();

  // Either a populated list or the explicit empty state — never a crash.
  const list = page.getByTestId("inbox-list");
  const empty = page.getByTestId("inbox-empty");
  await expect(list.or(empty)).toBeVisible();

  // The unread badge + pagination toolbar are always present.
  await expect(page.getByTestId("inbox-unread-badge")).toBeVisible();
  await expect(page.getByTestId("inbox-count")).toBeVisible();
});

test("marking an item read clears its unread dot", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/notifications/inbox");
  // Only show unread so the first row is guaranteed to have a mark-read button.
  await page.getByTestId("inbox-unread-only").check();

  const markButtons = page.getByTestId(/^inbox-mark-read-/);
  const count = await markButtons.count();
  test.skip(count === 0, "no unread notifications seeded for this tenant");

  await markButtons.first().click();
  // After invalidation the unread-only list shrinks (the item dropped out).
  await expect(async () => {
    const after = await page.getByTestId(/^inbox-mark-read-/).count();
    expect(after).toBeLessThan(count);
  }).toPass();
});

test("a dead-lettered notification can be retried from the inbox", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/notifications/inbox");
  await page.getByTestId("inbox-status-filter").selectOption("dead_letter");

  const retryButtons = page.getByTestId(/^inbox-retry-/);
  const count = await retryButtons.count();
  test.skip(count === 0, "no dead-lettered notifications seeded for this tenant");

  await retryButtons.first().click();
  // The retry re-enqueues; no error banner appears.
  await expect(page.getByTestId("inbox-retry-error")).toHaveCount(0);
});

test("mark-all-read empties the unread-only view", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/notifications/inbox");
  const markAll = page.getByTestId("inbox-mark-all-read");
  await expect(markAll).toBeVisible();

  // If nothing is unread the button is disabled — nothing to assert further.
  if (await markAll.isDisabled()) return;

  await markAll.click();
  await page.getByTestId("inbox-unread-only").check();
  await expect(page.getByTestId("inbox-empty")).toBeVisible();
});
