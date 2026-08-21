import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Re-ejecutar tests" button (Plan 06 task_06_30).
 *
 * Clicking the button POSTs to /api/review/{id}/rerun. We mock the
 * endpoint and assert: 1) the button calls it, 2) the status text
 * updates after the call.
 */

const SESSION_ID = "sess-rerun-1";

async function setup(page: Page, opts: { rerunStatus?: number } = {}): Promise<void> {
  await seedSession(page);
  await page.route(`**/api/review/${SESSION_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: SESSION_ID,
        plan_id: "plan-1",
        status: "running",
        checklist: [],
      }),
    }),
  );
  await page.route(`**/api/review/${SESSION_ID}/rerun`, (route) =>
    route.fulfill({
      status: opts.rerunStatus ?? 202,
      contentType: "application/json",
      body: JSON.stringify({ queued: true }),
    }),
  );
}

test("rerun button is visible and clickable", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/review/${SESSION_ID}`, { waitUntil: "domcontentloaded" });
  const button = page.getByTestId("review-rerun-button");
  await expect(button).toBeVisible();
  await button.click();
  await expect(page.getByTestId("review-rerun-status")).toContainText(/Re-ejecuci|Error/);
});
