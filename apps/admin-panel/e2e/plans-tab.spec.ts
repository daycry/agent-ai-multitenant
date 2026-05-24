import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the project "Planes" tab (Plan 03 task_03_17).
 *
 * Verifies:
 *   - Listing renders title + status badge.
 *   - Status filter chips count correctly and narrow the list.
 *   - Empty filter result shows the friendly empty-state.
 *   - A row links to the plan detail page (the link href is enough;
 *     the destination page lands in task_03_18).
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";

function plan(id: string, status: string, title: string) {
  return {
    id,
    tenant_id: "tttt0000-0000-0000-0000-000000000001",
    project_id: PROJECT_ID,
    title,
    description: `Descripción de ${title}`,
    status,
    conversation_id: null,
    specification: {},
    created_by: null,
    approved_by: null,
    approved_at: null,
    created_at: "2026-05-24T10:00:00Z",
    updated_at: "2026-05-24T10:00:00Z",
  };
}

const PLANS = [
  plan("11111111-1111-1111-1111-111111111111", "draft", "Plan A"),
  plan("22222222-2222-2222-2222-222222222222", "pending_approval", "Plan B"),
  plan("33333333-3333-3333-3333-333333333333", "approved", "Plan C"),
  plan("44444444-4444-4444-4444-444444444444", "completed", "Plan D"),
];

async function setup(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  // Use the full api-server URL so the mock cannot accidentally match
  // the admin-panel page route at /admin/projects/{id}/plans (which
  // shares the suffix). Pattern `**` here is intentional to avoid
  // intercepting our own Next.js navigation.
  await page.route(`http://localhost:8001/projects/${PROJECT_ID}/plans`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PLANS),
    });
  });
}

test("plans tab lists all plans with status badges and filter chips", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans`, {
    waitUntil: "domcontentloaded",
  });

  // 4 rows render.
  const rows = page.getByTestId("plans-list").locator("li");
  await expect(rows).toHaveCount(4);

  // Each plan title visible.
  for (const p of PLANS) {
    await expect(page.getByText(p.title, { exact: true })).toBeVisible();
  }

  // The "Todos" chip shows the total count.
  await expect(page.getByTestId("plans-filter-count-all")).toHaveText("(4)");
  // The "draft" chip shows 1 (Plan A).
  await expect(page.getByTestId("plans-filter-count-draft")).toHaveText("(1)");

  // The badge of Plan B reflects its pending_approval status.
  const badgeB = page.getByTestId(`plan-row-${PLANS[1].id}-badge`);
  await expect(badgeB).toHaveAttribute("data-status", "pending_approval");
  await expect(badgeB).toContainText("Pendiente");
});

test("filtering by approved narrows the list to one row", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("plans-filter-approved").click();
  await expect(page.getByTestId("plans-filter-approved")).toHaveAttribute("data-active", "true");

  const rows = page.getByTestId("plans-list").locator("li");
  await expect(rows).toHaveCount(1);
  await expect(page.getByText("Plan C", { exact: true })).toBeVisible();
  // The non-approved titles are hidden.
  await expect(page.getByText("Plan A", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Plan B", { exact: true })).toHaveCount(0);
});

test("filtering by a status with zero matches shows the empty-state", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("plans-filter-archived").click();
  await expect(page.getByTestId("plans-empty")).toContainText("Ningún plan en este estado");
  await expect(page.getByTestId("plans-list")).toHaveCount(0);
});

test("plan row links to the detail page", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans`, {
    waitUntil: "domcontentloaded",
  });

  const link = page.getByTestId(`plan-row-${PLANS[0].id}`);
  await expect(link).toHaveAttribute("href", `/admin/projects/${PROJECT_ID}/plans/${PLANS[0].id}`);
});
