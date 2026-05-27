import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the project edit dialog (Plan 06.6 task_06_6_02).
 *
 * Mocks GET + PUT /projects/{id} and verifies:
 *   - Clicking "Editar" opens the dialog with the current values.
 *   - Save fires a PUT with the typed payload.
 *   - Error responses surface in the dialog.
 */

const PROJECT_ID = "22222222-aaaa-bbbb-cccc-000000000001";

const PROJECT_FIXTURE = {
  id: PROJECT_ID,
  tenant_id: "tenant-1",
  name: "Antes de editar",
  description: "old description",
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

async function setup(
  page: Page,
  opts: { onPut?: (body: Record<string, unknown>) => void; putStatus?: number } = {},
): Promise<void> {
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
    if (route.request().method() === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPut?.(body);
      return route.fulfill({
        status: opts.putStatus ?? 200,
        contentType: "application/json",
        body: JSON.stringify({ ...PROJECT_FIXTURE, ...body }),
      });
    }
    return route.fallback();
  });
}

test("edit dialog opens with current values", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("project-edit-button").click();
  await expect(page.getByTestId("edit-project-name")).toHaveValue("Antes de editar");
  await expect(page.getByTestId("edit-project-description")).toHaveValue("old description");
  await expect(page.getByTestId("edit-project-status")).toHaveValue("active");
});

test("save sends PUT with updated payload", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  await setup(page, { onPut: (body) => calls.push(body) });
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("project-edit-button").click();
  await page.getByTestId("edit-project-name").fill("Nombre actualizado");
  await page.getByTestId("edit-project-description").fill("nueva descripción");
  await page.getByTestId("edit-project-status").selectOption("paused");
  await page.getByTestId("edit-project-save").click();

  await page.waitForTimeout(200);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    name: "Nombre actualizado",
    description: "nueva descripción",
    status: "paused",
  });
});

test("server error surfaces in dialog", async ({ page }) => {
  await setup(page, { putStatus: 422 });
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("project-edit-button").click();
  await page.getByTestId("edit-project-save").click();
  await expect(page.getByTestId("edit-project-error")).toBeVisible();
});

test("save button disabled when name is empty", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("project-edit-button").click();
  await page.getByTestId("edit-project-name").fill("");
  await expect(page.getByTestId("edit-project-save")).toBeDisabled();
});
