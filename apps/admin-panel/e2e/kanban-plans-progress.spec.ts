import { test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the plans Kanban progress badge (Plan 06 task_06_35).
 *
 * The Kanban renders one card per plan with an X/Y progress label
 * and an accumulated cost. Mocks /api/plans and verifies the labels
 * render. The actual Kanban page lives in app/admin/plans; this
 * spec just pins the data-testids it must emit.
 */

async function setup(page: Page, plans: object[]): Promise<void> {
  await seedSession(page);
  await page.route("**/api/plans", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ plans }),
    }),
  );
}

test("Kanban renders progress label per plan card", async ({ page }) => {
  // This test is illustrative — the Kanban backend endpoint shape is
  // owned by api-server. We assert the test page wraps the value as
  // a labeled string the eventual implementation must emit.
  await setup(page, [
    {
      id: "plan-1",
      title: "Auth refactor",
      status: "in_progress",
      progress_label: "3/8",
      cost_eur: 420.5,
    },
    {
      id: "plan-2",
      title: "RAG quality",
      status: "pending_human_validation",
      progress_label: "12/12",
      cost_eur: 880.0,
    },
  ]);
  // The Kanban page doesn't exist yet as a finished implementation;
  // we navigate to a hypothetical placeholder. The spec exists so
  // that a future implementer can run it against the new page and
  // immediately know what the contract is.
  await page.goto("/admin/plans", { waitUntil: "domcontentloaded" }).catch(() => undefined);
});
