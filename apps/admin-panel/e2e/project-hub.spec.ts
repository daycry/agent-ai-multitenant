import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the project hub page (Plan 06.6 task_06_6_01).
 *
 * Mocks GET /projects/{id} and verifies:
 *   - The hub page renders the project name + status.
 *   - All 6 sub-section cards are visible with their links.
 *   - Edit and Delete buttons exist.
 */

const PROJECT_ID = "11111111-aaaa-bbbb-cccc-000000000001";

const PROJECT_FIXTURE = {
  id: PROJECT_ID,
  tenant_id: "tenant-1",
  name: "Demo Plan 06.6 hub",
  description: "Proyecto sembrado para tests del hub",
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

async function setup(page: Page): Promise<void> {
  await seedSession(page);
  await page.route(apiRoute(`/projects/${PROJECT_ID}`), (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECT_FIXTURE),
    });
  });
}

test("project hub renders header + 6 sub-section cards", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("project-hub")).toBeVisible();
  await expect(page.getByTestId("page-title")).toContainText("Demo Plan 06.6 hub");
  await expect(page.getByTestId("project-status-row")).toContainText("active");

  // All 6 sub-sections render.
  for (const key of [
    "chat",
    "plans",
    "knowledge-bases",
    "mcp-servers",
    "agent-tools-diagnostic",
    "dep-cache",
  ]) {
    await expect(page.getByTestId(`project-section-${key}`)).toBeVisible();
  }
});

test("sub-section cards link to the right path", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  const link = page.getByTestId("project-section-link-mcp-servers");
  await expect(link).toHaveAttribute("href", `/admin/projects/${PROJECT_ID}/mcp-servers`);
});

test("edit + delete buttons are visible when project loaded", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("project-edit-button")).toBeVisible();
  await expect(page.getByTestId("project-delete-button")).toBeVisible();
});

test("404 from backend shows error card with back link", async ({ page }) => {
  await seedSession(page);
  await page.route(apiRoute(`/projects/${PROJECT_ID}`), (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Project not found" }),
    }),
  );
  await page.goto(`/admin/projects/${PROJECT_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("project-error")).toBeVisible();
});
