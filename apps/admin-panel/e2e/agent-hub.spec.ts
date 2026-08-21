import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the agent hub page (Plan 06.6 task_06_6_05).
 */

const AGENT_ID = "aaaa1111-bbbb-2222-cccc-333333333333";

const TENANT_AGENT = {
  id: AGENT_ID,
  tenant_id: "t",
  name: "Backend Senior",
  description: "Plantilla del tenant",
  agent_type: "ai",
  role: "backend_dev",
  system_prompt: "Eres un backend senior, …",
  memory_scope: "private",
  review_capability: false,
  max_concurrent_tasks: 2,
  is_template: true,
  scope: "global_tenant_template",
  project_id: null,
  forked_from_agent_id: null,
};

async function setup(page: Page, agent: object = TENANT_AGENT): Promise<void> {
  await seedSession(page);
  await page.route(apiRoute(`/agents/${AGENT_ID}`), (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(agent),
    });
  });
}

test("hub renders name, role + system prompt", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("agent-hub")).toBeVisible();
  await expect(page.getByTestId("page-title")).toContainText("Backend Senior");
  await expect(page.getByTestId("agent-fields")).toContainText("backend_dev");
  await expect(page.getByTestId("agent-fields")).toContainText("Eres un backend senior");
});

test("tenant template shows edit + delete buttons", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("agent-edit-button")).toBeVisible();
  await expect(page.getByTestId("agent-delete-button")).toBeVisible();
});

test("built-in agent is shown read-only (no edit/delete)", async ({ page }) => {
  const builtin = { ...TENANT_AGENT, scope: "global_builtin", is_template: true };
  await setup(page, builtin);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("agent-hub")).toBeVisible();
  await expect(page.getByTestId("agent-edit-button")).toHaveCount(0);
  await expect(page.getByTestId("agent-delete-button")).toHaveCount(0);
});
