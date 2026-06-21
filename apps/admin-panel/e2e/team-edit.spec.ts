import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for team edit dialog (Plan 06.6 task_06_6_08).
 */

const TEAM_ID = "team1111-aaaa-bbbb-cccc-dddddddddddd";
const TEAM_NAME = "Frontend Squad";

const TEAM_FIXTURE = {
  id: TEAM_ID,
  tenant_id: "t",
  name: TEAM_NAME,
  description: "Frontend team description",
  default_workflow_template_id: null,
  is_builtin: false,
  members: [],
  created_at: "2026-05-27T12:00:00Z",
  updated_at: "2026-05-27T12:00:00Z",
  deleted_at: null,
};

async function setup(
  page: Page,
  opts: { onPut?: (body: Record<string, unknown>) => void } = {},
): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`**/teams/${TEAM_ID}`, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TEAM_FIXTURE),
      });
    }
    if (route.request().method() === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPut?.(body);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...TEAM_FIXTURE, ...body }),
      });
    }
    return route.fallback();
  });
  // The detail page also fetches /agents and /projects.
  await page.route("**/agents", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
}

test("edit dialog opens with current name + description", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/teams/${TEAM_ID}`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("team-edit-button").click();
  await expect(page.getByTestId("edit-team-name")).toHaveValue(TEAM_NAME);
  await expect(page.getByTestId("edit-team-description-edit")).toHaveValue(
    "Frontend team description",
  );
});

test("save sends PUT with updated fields", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  await setup(page, { onPut: (body) => calls.push(body) });
  await page.goto(`/admin/teams/${TEAM_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("team-edit-button").click();
  await page.getByTestId("edit-team-name").fill("Frontend Squad v2");
  await page.getByTestId("edit-team-description-edit").fill("new desc");
  await page.getByTestId("edit-team-save").click();

  await page.waitForTimeout(200);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    name: "Frontend Squad v2",
    description: "new desc",
  });
});
