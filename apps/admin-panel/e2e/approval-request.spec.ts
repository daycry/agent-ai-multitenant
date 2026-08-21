import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the approval-request notification (task_02_25).
 *
 * A pending approval request surfaces in-app on the Aprobaciones page:
 * a card in the feed and a count. Self-contained — the JWT is injected
 * into localStorage and `GET /approvals` is mocked.
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
  await page.route("http://localhost:8001/approvals", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    }),
  );
}

test("a pending approval request shows up as an in-app notification", async ({ page }) => {
  await setup(page, [REQUEST]);
  await page.goto("/admin/approvals", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("approvals-list")).toBeVisible();
  await expect(page.getByTestId(`approval-card-${REQUEST.id}`)).toBeVisible();
  await expect(page.getByTestId(`approval-category-${REQUEST.id}`)).toHaveText("production_deploy");
  // The pending count is the notification badge.
  await expect(page.getByTestId("approval-count")).toHaveText("1");
});

test("the feed shows an empty state when nothing is pending", async ({ page }) => {
  await setup(page, []);
  await page.goto("/admin/approvals", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("approvals-empty")).toBeVisible();
  await expect(page.getByTestId("approval-count")).toHaveText("0");
});
