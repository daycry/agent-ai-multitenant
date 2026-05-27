import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for plan → escalated/review deep links (Plan 06.6 task_06_6_12).
 */

const PROJECT_ID = "proj-link-1";
const PLAN_ID = "plan-link-1";

function planFixture(status: string): object {
  return {
    id: PLAN_ID,
    project_id: PROJECT_ID,
    title: "Plan con deep links",
    description: "para testear los links",
    status,
    specification: {
      summary: "summary",
      phases: [],
      tasks: [],
      estimates: null,
    },
    created_at: "2026-05-27T12:00:00Z",
    updated_at: "2026-05-27T12:00:00Z",
  };
}

async function setup(page: Page, planStatus: string): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`**/plans/${PLAN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(planFixture(planStatus)),
    }),
  );
  // Cost breakdown sub-section fetches; mock it as empty.
  await page.route(`**/plans/${PLAN_ID}/cost*`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({}),
    }),
  );
}

test("plan detail shows escalated tasks link always", async ({ page }) => {
  await setup(page, "in_progress");
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-deep-links")).toBeVisible();
  await expect(page.getByTestId("plan-link-escalated")).toHaveAttribute(
    "href",
    `/admin/plans/${PLAN_ID}/escalated`,
  );
});

test("plan in pending_human_validation also shows review session link", async ({ page }) => {
  await setup(page, "pending_human_validation");
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-link-review")).toBeVisible();
});

test("plan not in pending_human_validation hides review session link", async ({ page }) => {
  await setup(page, "in_progress");
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-link-review")).toHaveCount(0);
});
