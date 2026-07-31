import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Añadir tarea libre al plan" form (Plan 06 task_06_34b5).
 *
 * The escalated-tasks page hosts the form (one panel, two responsibilities).
 * Mocks /api/plans/{id}/free-task and verifies: 1) the inputs +
 * submit button render, 2) clicking submit posts the expected body,
 * 3) the status text updates on success.
 */

const PLAN_ID = "plan-free-1";

async function setup(page: Page, opts: { onSubmit?: (req: object) => void } = {}): Promise<void> {
  await seedSession(page);
  await page.route(`**/api/plans/${PLAN_ID}/escalated-tasks`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tasks: [] }),
    }),
  );
  await page.route(`**/api/plans/${PLAN_ID}/free-task`, async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    opts.onSubmit?.(body);
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ id: "task-new" }),
    });
  });
}

test("form is visible on the escalated page", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("free-task-form")).toBeVisible();
  await expect(page.getByTestId("free-task-title")).toBeVisible();
  await expect(page.getByTestId("free-task-description")).toBeVisible();
  await expect(page.getByTestId("free-task-submit")).toBeVisible();
});

test("submit posts {title, description} to /free-task", async ({ page }) => {
  const submissions: object[] = [];
  await setup(page, { onSubmit: (req) => submissions.push(req) });
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("free-task-title").fill("Refactor auth middleware");
  await page.getByTestId("free-task-description").fill("Compliance flagged sessions");
  await page.getByTestId("free-task-submit").click();
  await expect(page.getByTestId("free-task-status")).toContainText(/creada/i);
  expect(submissions[0]).toMatchObject({
    title: "Refactor auth middleware",
    description: "Compliance flagged sessions",
  });
});

test("missing title shows error", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("free-task-submit").click();
  await expect(page.getByTestId("free-task-status")).toContainText(/título/i);
});
