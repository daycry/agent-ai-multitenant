import { expect, test, type Page } from "@playwright/test";

/**
 * E2E: the "Depende de" section of the task detail sheet must show dependency
 * TITLES (resolved from the project's task list), not internal UUIDs.
 */

const PROJECT_ID = "proj-deps-1";
const MAIN_ID = "task-main-1";
const DEP_ID = "task-dep-1";
const API = "http://localhost:8001";

function taskRow(
  id: string,
  title: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    id,
    tenant_id: "t1",
    project_id: PROJECT_ID,
    plan_id: null,
    title,
    description: null,
    status: "backlog",
    priority: "medium",
    assigned_agent_id: null,
    reviewer_agent_id: null,
    acceptance_criteria: [],
    inputs: {},
    estimated_complexity: null,
    retry_count: 0,
    max_retries: 3,
    started_at: null,
    completed_at: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    depends_on: [],
    ...extra,
  };
}

async function setup(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });

  const main = taskRow(MAIN_ID, "Tarea principal", { depends_on: [DEP_ID] });
  const dep = taskRow(DEP_ID, "Definir contrato de respuesta JSON");

  await page.route(`${API}/projects/${PROJECT_ID}/tasks`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([main, dep]),
    }),
  );
  await page.route(`${API}/projects/${PROJECT_ID}/plans`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`${API}/runs**`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`${API}/projects/${PROJECT_ID}/tasks/${MAIN_ID}`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(main) }),
  );
}

test("renders dependency titles instead of internal UUIDs", async ({ page }) => {
  test.setTimeout(60_000);
  await setup(page);

  await page.goto(`/admin/projects/${PROJECT_ID}/tasks`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("view-toggle-kanban").click();
  await page.getByTestId(`tasks-card-${MAIN_ID}`).click();
  await expect(page.getByTestId("task-detail-sheet")).toBeVisible();

  const deps = page.getByTestId("task-detail-deps");
  await expect(deps).toBeVisible();
  // The dependency's TITLE is shown...
  await expect(deps.getByText("Definir contrato de respuesta JSON")).toBeVisible();
  // ...and the raw UUID is NOT.
  await expect(deps.getByText(DEP_ID)).toHaveCount(0);
});
