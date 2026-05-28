import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the agent edit + delete dialogs (Plan 06.6 task_06_6_07).
 */

const AGENT_ID = "edit11111-aaaa-bbbb-cccc-dddddddddddd";
const AGENT_NAME = "Frontend Senior";

const AGENT_FIXTURE = {
  id: AGENT_ID,
  tenant_id: "t",
  name: AGENT_NAME,
  description: "Plantilla",
  agent_type: "ai",
  role: "frontend_dev",
  system_prompt: "Eres un FE senior.",
  memory_scope: "private",
  review_capability: false,
  max_concurrent_tasks: 1,
  is_template: true,
  scope: "global_tenant_template",
  project_id: null,
  forked_from_agent_id: null,
};

async function setup(
  page: Page,
  opts: {
    onPut?: (body: Record<string, unknown>) => void;
    onDelete?: () => void;
  } = {},
): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/agents", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    }),
  );
  await page.route(`**/agents/${AGENT_ID}`, async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(AGENT_FIXTURE),
      });
    }
    if (method === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPut?.(body);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...AGENT_FIXTURE, ...body }),
      });
    }
    if (method === "DELETE") {
      opts.onDelete?.();
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });
}

test("edit dialog pre-fills current values", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("agent-edit-button").click();
  await expect(page.getByTestId("edit-agent-name")).toHaveValue(AGENT_NAME);
  await expect(page.getByTestId("edit-agent-role")).toHaveValue("frontend_dev");
  await expect(page.getByTestId("edit-agent-system-prompt")).toHaveValue("Eres un FE senior.");
});

test("save sends PUT with updated payload", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  await setup(page, { onPut: (body) => calls.push(body) });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-edit-button").click();
  await page.getByTestId("edit-agent-name").fill("Frontend Senior v2");
  await page.getByTestId("edit-agent-review-cap").check();
  await page.getByTestId("edit-agent-max-tasks").fill("4");
  await page.getByTestId("edit-agent-save").click();

  await page.waitForTimeout(200);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    name: "Frontend Senior v2",
    review_capability: true,
    max_concurrent_tasks: 4,
  });
});

test("delete with confirm-by-name fires DELETE", async ({ page }) => {
  let deleted = false;
  await setup(page, { onDelete: () => (deleted = true) });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-delete-button").click();
  await expect(page.getByTestId("delete-agent-confirm")).toBeDisabled();

  await page.getByTestId("delete-agent-confirm-input").fill(AGENT_NAME);
  await expect(page.getByTestId("delete-agent-confirm")).toBeEnabled();
  await page.getByTestId("delete-agent-confirm").click();

  await page.waitForURL("**/admin/agents", { timeout: 3000 });
  expect(deleted).toBe(true);
});
