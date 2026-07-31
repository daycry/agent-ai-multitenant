import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the logs WebSocket pane (Plan 06 task_06_29).
 *
 * The real WS endpoint is served by api-server; this spec pins the
 * client-side rendering. The pane exists; it shows whatever
 * messages have been received so far.
 */

const SESSION_ID = "sess-logs-1";

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

test("logs pane exists and is wired", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/review/${SESSION_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("review-logs")).toBeVisible();
  await expect(page.getByTestId("review-logs-pane")).toBeVisible();
});
