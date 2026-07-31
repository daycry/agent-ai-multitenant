import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the escalated-tasks panel (Plan 06 task_06_34b3).
 *
 * Mocks /api/plans/{id}/escalated-tasks + /api/tasks/{id}/human-action
 * and verifies: 1) one row per escalated task, 2) all four buttons
 * render, 3) clicking a button calls the endpoint.
 */

const PLAN_ID = "plan-esc-1";

async function setup(
  page: Page,
  opts: { tasks?: object[]; onAction?: (req: object) => void } = {},
): Promise<void> {
  const tasks = opts.tasks ?? [];
  const onAction = opts.onAction;
  await seedSession(page);
  await page.route(`**/api/plans/${PLAN_ID}/escalated-tasks`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tasks }),
    }),
  );
  await page.route(`**/api/tasks/*/human-action`, async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    onAction?.(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
}

const SAMPLE = {
  id: "task-1",
  title: "Implementar webhook",
  description: "Endpoint rechaza el POST con 500",
  retry_count: 3,
  history: [],
};

test("empty state when no escalated tasks", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("escalated-empty")).toBeVisible();
});

test("renders one row per escalated task with four action buttons", async ({ page }) => {
  await setup(page, { tasks: [SAMPLE] });
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("escalated-task-1")).toBeVisible();
  await expect(page.getByTestId("approve-task-1")).toBeVisible();
  await expect(page.getByTestId("reassign-task-1")).toBeVisible();
  await expect(page.getByTestId("block-task-1")).toBeVisible();
  await expect(page.getByTestId("cancel-task-1")).toBeVisible();
});

test("clicking approve calls the endpoint with action=approve_manual", async ({ page }) => {
  const calls: object[] = [];
  await setup(page, { tasks: [SAMPLE], onAction: (req) => calls.push(req) });
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("approve-task-1").click();
  await page.waitForTimeout(100);
  expect(calls[0]).toMatchObject({ action: "approve_manual" });
});
