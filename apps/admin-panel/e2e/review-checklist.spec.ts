import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the human checklist (Plan 06 task_06_31).
 *
 * The page fetches the plan's ``human_*`` items and renders one
 * checkbox per item. Clicking toggles the local state — the actual
 * "approve / reject the whole plan" submit lives in the parent
 * page (covered by Fase G2 later).
 */

const SESSION_ID = "sess-checklist-1";

const CHECKLIST = [
  {
    id: "human_06_01",
    description: "End-to-end cycle of a plan with a real repo",
    hint: "Run a plan touching a Laravel project",
    checklist: ["Branch created", "PR opened", "Tests green"],
  },
  {
    id: "human_06_02",
    description: "Dependency cache works",
    hint: null,
    checklist: ["First run normal", "Second run fast"],
  },
];

async function setup(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`**/api/review/${SESSION_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: SESSION_ID,
        plan_id: "plan-1",
        status: "running",
        checklist: CHECKLIST,
      }),
    }),
  );
}

test("checklist renders one item per human_* entry", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/review/${SESSION_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("review-checklist-item-human_06_01")).toBeVisible();
  await expect(page.getByTestId("review-checklist-item-human_06_02")).toBeVisible();
  await expect(page.getByTestId("review-checklist-item-human_06_01")).toContainText(
    "End-to-end cycle",
  );
});

test("clicking a checkbox toggles its state", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/review/${SESSION_ID}`, { waitUntil: "domcontentloaded" });
  const box = page.getByTestId("review-checkbox-human_06_01");
  await expect(box).not.toBeChecked();
  await box.check();
  await expect(box).toBeChecked();
});
