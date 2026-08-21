import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the review-runtime terminal pane (Plan 06 task_06_28).
 *
 * We don't drive a real ttyd here — that requires a docker daemon
 * + a running review-runtime. We pin the rendered shell: a
 * ``[data-testid=review-terminal-pane]`` element exists on the page
 * and shows the connecting/connected state.
 */

const SESSION_ID = "sess-terminal-1";

async function setup(page: Page): Promise<void> {
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
}

test("terminal pane is rendered on the review page", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/review/${SESSION_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("review-terminal")).toBeVisible();
  await expect(page.getByTestId("review-terminal-pane")).toBeVisible();
});
