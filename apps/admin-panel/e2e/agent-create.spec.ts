import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Nuevo agente" dialog (Plan 06.6 task_06_6_06).
 */

async function setup(
  page: Page,
  opts: { onPost?: (body: Record<string, unknown>) => void } = {},
): Promise<void> {
  await seedSession(page);
  await page.route("**/agents", async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    }
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPost?.(body);
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "new-agent-1",
          tenant_id: "t",
          ...body,
          agent_type: "ai",
          memory_scope: "private",
          review_capability: false,
          max_concurrent_tasks: 1,
          is_template: body.scope === "global_tenant_template",
          forked_from_agent_id: null,
        }),
      });
    }
    return route.fallback();
  });
}

test("nuevo agente button is visible in the catalog header", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("new-agent-button")).toBeVisible();
});

test("dialog opens with default scope=global_tenant_template", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-agent-button").click();
  await expect(page.getByTestId("new-agent-scope-template")).toBeChecked();
  // project_id input is hidden by default.
  await expect(page.getByTestId("new-agent-project-id")).toHaveCount(0);
});

test("switching to project_local reveals project_id input", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-agent-button").click();
  await page.getByTestId("new-agent-scope-local").check();
  await expect(page.getByTestId("new-agent-project-id")).toBeVisible();
});

test("submit posts the payload with selected scope", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  await setup(page, { onPost: (body) => calls.push(body) });
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });

  await page.getByTestId("new-agent-button").click();
  await page.getByTestId("new-agent-name").fill("Agente Test");
  await page.getByTestId("new-agent-role").selectOption("reviewer");
  await page.getByTestId("new-agent-system-prompt").fill("Eres un revisor estricto.");
  await page.getByTestId("new-agent-submit").click();

  await page.waitForTimeout(200);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    name: "Agente Test",
    role: "reviewer",
    system_prompt: "Eres un revisor estricto.",
    scope: "global_tenant_template",
    project_id: null,
  });
});

test("submit disabled when project_local + no project_id", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-agent-button").click();
  await page.getByTestId("new-agent-name").fill("X");
  await page.getByTestId("new-agent-system-prompt").fill("Y");
  await page.getByTestId("new-agent-scope-local").check();
  await expect(page.getByTestId("new-agent-submit")).toBeDisabled();
});
