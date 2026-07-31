import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for team delete with confirm-by-name (Plan 06.6 task_06_6_09).
 */

const TEAM_ID = "del-team-1-aaaa-bbbb-cccc-dddddddddddd";
const TEAM_NAME = "Equipo a borrar";

const TEAM_FIXTURE = {
  id: TEAM_ID,
  tenant_id: "t",
  name: TEAM_NAME,
  description: null,
  default_workflow_template_id: null,
  is_builtin: false,
  members: [],
  created_at: "2026-05-27T12:00:00Z",
  updated_at: "2026-05-27T12:00:00Z",
  deleted_at: null,
};

async function setup(page: Page, opts: { onDelete?: () => void } = {}): Promise<void> {
  await seedSession(page);
  await page.route(`**/teams/${TEAM_ID}`, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TEAM_FIXTURE),
      });
    }
    if (route.request().method() === "DELETE") {
      opts.onDelete?.();
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });
  await page.route("**/agents", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/teams", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
}

test("confirm disabled until name matches; matching name fires DELETE", async ({ page }) => {
  let deleted = false;
  await setup(page, { onDelete: () => (deleted = true) });
  await page.goto(`/admin/teams/${TEAM_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("team-delete-button").click();
  await expect(page.getByTestId("delete-team-confirm")).toBeDisabled();

  await page.getByTestId("delete-team-confirm-input").fill(TEAM_NAME);
  await expect(page.getByTestId("delete-team-confirm")).toBeEnabled();
  await page.getByTestId("delete-team-confirm").click();

  await page.waitForURL("**/admin/teams", { timeout: 3000 });
  expect(deleted).toBe(true);
});

test("builtin team hides edit/delete buttons", async ({ page }) => {
  const builtin = { ...TEAM_FIXTURE, is_builtin: true };
  await seedSession(page);
  await page.route(`**/teams/${TEAM_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(builtin),
    }),
  );
  await page.route("**/agents", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  await page.goto(`/admin/teams/${TEAM_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("team-edit-button")).toHaveCount(0);
  await expect(page.getByTestId("team-delete-button")).toHaveCount(0);
});
