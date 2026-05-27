import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the project delete dialog with confirm-by-name
 * (Plan 06.6 task_06_6_03).
 *
 * Verifies:
 *   - Confirm button stays disabled until the typed name matches.
 *   - Mismatched name → no DELETE call.
 *   - Exact match → DELETE fires + redirect to /admin/projects.
 */

const PROJECT_ID = "33333333-aaaa-bbbb-cccc-000000000001";
const PROJECT_NAME = "Pildora delicada";

const PROJECT_FIXTURE = {
  id: PROJECT_ID,
  tenant_id: "t",
  name: PROJECT_NAME,
  description: null,
  status: "active",
  team_id: null,
  is_template: false,
  mcp_servers: [],
  rag_knowledge_bases: [],
  worker_config: {},
  repository_config: null,
  human_approval_policy: null,
  secrets_vault_id: null,
  budget_amount: null,
  budget_currency: null,
  budget_period: null,
  budget_period_start_day: null,
  budget_period_length_days: null,
  paused_by_budget: false,
  created_at: "2026-05-27T12:00:00Z",
  updated_at: "2026-05-27T12:00:00Z",
  deleted_at: null,
};

async function setup(page: Page, opts: { onDelete?: () => void } = {}): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`**/projects/${PROJECT_ID}`, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(PROJECT_FIXTURE),
      });
    }
    if (route.request().method() === "DELETE") {
      opts.onDelete?.();
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });
  // The redirect target.
  await page.route("**/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
}

test("confirm button disabled until name matches", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("project-delete-button").click();
  const confirm = page.getByTestId("delete-project-confirm");
  await expect(confirm).toBeDisabled();

  await page.getByTestId("delete-project-confirm-input").fill("nombre equivocado");
  await expect(confirm).toBeDisabled();

  await page.getByTestId("delete-project-confirm-input").fill(PROJECT_NAME);
  await expect(confirm).toBeEnabled();
});

test("matching name fires DELETE and redirects", async ({ page }) => {
  let deleted = false;
  await setup(page, { onDelete: () => (deleted = true) });
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("project-delete-button").click();
  await page.getByTestId("delete-project-confirm-input").fill(PROJECT_NAME);
  await page.getByTestId("delete-project-confirm").click();

  await page.waitForURL("**/admin/projects", { timeout: 3000 });
  expect(deleted).toBe(true);
});

test("cancel button closes dialog without deleting", async ({ page }) => {
  let deleted = false;
  await setup(page, { onDelete: () => (deleted = true) });
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("project-delete-button").click();
  await page.getByTestId("delete-project-confirm-input").fill(PROJECT_NAME);
  // Click Cancel (the only other button with role=button in the dialog footer).
  await page.getByRole("button", { name: /cancelar/i }).click();

  // Re-open: input should be cleared (state reset).
  await page.getByTestId("project-delete-button").click();
  await expect(page.getByTestId("delete-project-confirm-input")).toHaveValue("");
  expect(deleted).toBe(false);
});
