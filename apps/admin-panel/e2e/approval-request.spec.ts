import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the approval-request notification (task_02_25).
 *
 * A pending approval request surfaces in-app on the Aprobaciones tab of the
 * unified human inbox (`/admin/approvals` redirects to `/admin/inbox?tab=approvals`):
 * a card in the feed and a count on the tab. Self-contained — the JWT is injected
 * into localStorage and every source of the inbox is mocked.
 */

const REQUEST = {
  id: "aa000000-0000-0000-0000-000000000001",
  execution_id: "ee000000-0000-0000-0000-000000000001",
  task_id: "22222222-2222-2222-2222-222222222222",
  project_id: "33333333-3333-3333-3333-333333333333",
  category: "production_deploy",
  action: { tool: "shell_exec", args: { command: "deploy.sh" } },
  status: "pending",
  requested_at: "2026-05-22T10:00:00Z",
};

async function setup(page: Page, body: unknown) {
  await seedSession(page);
  // Exact origin — a `**/approvals` glob would also catch the
  // `/admin/approvals` page navigation.
  const ok = (payload: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
  await page.route("http://localhost:8001/approvals", (route) => route.fulfill(ok(body)));
  // The other sources of the inbox: empty (their errors would not read as «0»).
  await page.route("http://localhost:8001/human-queue", (route) => route.fulfill(ok([])));
  await page.route("http://localhost:8001/inbox/reviews**", (route) => route.fulfill(ok([])));
  await page.route("http://localhost:8001/inbox/assignments", (route) => route.fulfill(ok([])));
  await page.route("http://localhost:8001/notifications/logs**", (route) =>
    route.fulfill(ok({ items: [], total: 0, unread: 0, limit: 1, offset: 0 })),
  );
}

test("a pending approval request shows up as an in-app notification", async ({ page }) => {
  await setup(page, [REQUEST]);
  await page.goto("/admin/approvals", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/admin\/inbox\?tab=approvals/);

  await expect(page.getByTestId("approvals-list")).toBeVisible();
  await expect(page.getByTestId(`approval-card-${REQUEST.id}`)).toBeVisible();
  await expect(page.getByTestId(`approval-category-${REQUEST.id}`)).toHaveText("production_deploy");
  // The pending count is the tab's badge.
  await expect(page.getByTestId("inbox-count-approvals")).toHaveText("1");
});

test("the feed shows an empty state when nothing is pending", async ({ page }) => {
  await setup(page, []);
  await page.goto("/admin/approvals", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/admin\/inbox\?tab=approvals/);

  await expect(page.getByTestId("approvals-empty")).toBeVisible();
  await expect(page.getByTestId("inbox-count-approvals")).toHaveText("0");
  await expect(page.getByTestId("inbox-all-clear")).toBeVisible();
});
