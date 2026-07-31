import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for editing a task's acceptance criteria from the TaskDetailSheet.
 *
 * The sheet opens from a Kanban card on the project tasks page. The backend is
 * mocked: `GET /projects/{pid}/tasks/{tid}` returns the current criteria and
 * `PUT` persists them (the mock keeps an in-memory copy so the re-fetch after
 * save reflects the new list). Covers: fill from empty, edit + remove existing,
 * and preserving a structured (dict) criterion through a round-trip.
 */

const PROJECT_ID = "proj-crit-1";
const TASK_ID = "task-crit-1";

function taskRow(criteria: unknown[]): Record<string, unknown> {
  return {
    id: TASK_ID,
    tenant_id: "t1",
    project_id: PROJECT_ID,
    plan_id: null,
    title: "Tarea con criterios",
    description: "Descripción de la tarea",
    status: "backlog",
    priority: "medium",
    assigned_agent_id: null,
    reviewer_agent_id: null,
    acceptance_criteria: criteria,
    inputs: {},
    estimated_complexity: null,
    retry_count: 0,
    max_retries: 3,
    started_at: null,
    completed_at: null,
    created_at: "2026-06-30T00:00:00Z",
    updated_at: "2026-06-30T00:00:00Z",
    depends_on: [],
  };
}

async function setup(
  page: Page,
  opts: {
    initialCriteria?: unknown[];
    onPut?: (body: { acceptance_criteria: unknown[] }) => void;
  } = {},
): Promise<void> {
  const state = { criteria: opts.initialCriteria ?? [] };

  await seedSession(page);

  // Mocks are anchored to the API origin (:8001) — NOT a bare `**/projects/...`
  // glob, which would also intercept the page navigation `:3000/admin/projects/
  // {id}/tasks` and serve raw JSON instead of the Next page.
  const API = "http://localhost:8001";

  // Kanban + list queries on the tasks page.
  await page.route(`${API}/projects/${PROJECT_ID}/tasks`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([taskRow(state.criteria)]),
    }),
  );
  await page.route(`${API}/projects/${PROJECT_ID}/plans`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`${API}/runs**`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  // Task detail GET (current criteria) + PUT (persist). Registered AFTER the
  // list route so it wins for the `/tasks/{id}` URL.
  await page.route(`${API}/projects/${PROJECT_ID}/tasks/${TASK_ID}`, async (route) => {
    if (route.request().method() === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      state.criteria = body.acceptance_criteria ?? [];
      opts.onPut?.(body);
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(taskRow(state.criteria)),
    });
  });
}

async function openSheet(page: Page): Promise<void> {
  // First hit compiles the route under `next dev`; give it slack.
  test.setTimeout(60_000);
  await page.goto(`/admin/projects/${PROJECT_ID}/tasks`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("view-toggle-kanban").click();
  await page.getByTestId(`tasks-card-${TASK_ID}`).click();
  await expect(page.getByTestId("task-detail-sheet")).toBeVisible();
}

test("fills acceptance criteria from an empty task and persists them", async ({ page }) => {
  const puts: { acceptance_criteria: unknown[] }[] = [];
  await setup(page, { initialCriteria: [], onPut: (b) => puts.push(b) });
  await openSheet(page);

  // Empty state + Editar affordance present.
  await expect(page.getByTestId("task-criteria-empty")).toBeVisible();
  await page.getByTestId("task-criteria-edit").click();

  // Add two criteria from scratch.
  await page.getByTestId("task-criterion-add").click();
  await page
    .getByTestId("task-criterion-row-0")
    .getByTestId("task-criterion-input")
    .fill("composer audit no reporta vulnerabilidades");
  await page.getByTestId("task-criterion-add").click();
  await page
    .getByTestId("task-criterion-row-1")
    .getByTestId("task-criterion-input")
    .fill("la suite PHPUnit pasa en verde");

  await page.getByTestId("task-criteria-save").click();

  // PUT carried exactly the two strings...
  await expect.poll(() => puts.length).toBe(1);
  expect(puts[0].acceptance_criteria).toEqual([
    "composer audit no reporta vulnerabilidades",
    "la suite PHPUnit pasa en verde",
  ]);

  // ...and the sheet re-renders read mode with them.
  const criteria = page.getByTestId("task-detail-criteria");
  await expect(criteria.getByText("composer audit no reporta vulnerabilidades")).toBeVisible();
  await expect(criteria.getByText("la suite PHPUnit pasa en verde")).toBeVisible();
});

test("edits and removes existing criteria, preserving a structured one", async ({ page }) => {
  const puts: { acceptance_criteria: unknown[] }[] = [];
  await setup(page, {
    initialCriteria: ["uno", "dos", { id: "c1", description: "tres" }],
    onPut: (b) => puts.push(b),
  });
  await openSheet(page);

  await page.getByTestId("task-criteria-edit").click();

  // Remove the middle one ("dos") and edit the first.
  await page.getByTestId("task-criterion-remove-1").click();
  await page
    .getByTestId("task-criterion-row-0")
    .getByTestId("task-criterion-input")
    .fill("uno-editado");

  await page.getByTestId("task-criteria-save").click();

  await expect.poll(() => puts.length).toBe(1);
  // The string row is edited; the dict criterion keeps its structure (id) with
  // its description preserved.
  expect(puts[0].acceptance_criteria).toEqual(["uno-editado", { id: "c1", description: "tres" }]);
});
