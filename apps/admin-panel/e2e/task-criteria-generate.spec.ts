import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for AI-generating a task's acceptance criteria from the TaskDetailSheet.
 *
 * The backend is mocked. `POST .../generate-acceptance-criteria` returns a
 * proposal WITHOUT persisting; the UI then either (empty task) preloads the
 * editor for review, or (task already has criteria) opens a comparison modal
 * that the operator accepts or cancels — never overwriting silently. Save uses
 * the existing PUT. Routes are anchored to the API origin (:8001) so the bare
 * page navigation to :3000 is not intercepted.
 */

const PROJECT_ID = "proj-gen-1";
const TASK_ID = "task-gen-1";
const API = "http://localhost:8001";

function taskRow(criteria: unknown[]): Record<string, unknown> {
  return {
    id: TASK_ID,
    tenant_id: "t1",
    project_id: PROJECT_ID,
    plan_id: null,
    title: "Tarea a generar",
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
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    depends_on: [],
  };
}

async function setup(
  page: Page,
  opts: {
    initialCriteria?: unknown[];
    proposal?: string[];
    generateStatus?: number;
    onPut?: (body: { acceptance_criteria: unknown[] }) => void;
  } = {},
): Promise<void> {
  const state = { criteria: opts.initialCriteria ?? [] };
  const proposal = opts.proposal ?? ["criterio generado 1", "criterio generado 2"];

  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });

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

  // Generate (POST) — returns a proposal, never persists.
  await page.route(
    `${API}/projects/${PROJECT_ID}/tasks/${TASK_ID}/generate-acceptance-criteria`,
    (route) =>
      route.fulfill({
        status: opts.generateStatus ?? 200,
        contentType: "application/json",
        body:
          (opts.generateStatus ?? 200) === 200
            ? JSON.stringify({ acceptance_criteria: proposal })
            : JSON.stringify({ detail: "No hay proveedor LLM configurado." }),
      }),
  );

  // Task detail GET (current criteria) + PUT (persist).
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
  test.setTimeout(60_000);
  await page.goto(`/admin/projects/${PROJECT_ID}/tasks`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("view-toggle-kanban").click();
  await page.getByTestId(`tasks-card-${TASK_ID}`).click();
  await expect(page.getByTestId("task-detail-sheet")).toBeVisible();
}

test("generates criteria for an empty task and preloads the editor", async ({ page }) => {
  const puts: { acceptance_criteria: unknown[] }[] = [];
  await setup(page, {
    initialCriteria: [],
    proposal: ["composer audit sin vulnerabilidades", "la suite PHPUnit pasa"],
    onPut: (b) => puts.push(b),
  });
  await openSheet(page);

  await expect(page.getByTestId("task-criteria-empty")).toBeVisible();
  await page.getByTestId("task-criteria-generate").click();

  // No comparison modal (nothing to overwrite) — the editor is preloaded.
  await expect(page.getByTestId("task-criteria-compare")).toHaveCount(0);
  await expect(
    page.getByTestId("task-criterion-row-0").getByTestId("task-criterion-input"),
  ).toHaveValue("composer audit sin vulnerabilidades");
  await expect(
    page.getByTestId("task-criterion-row-1").getByTestId("task-criterion-input"),
  ).toHaveValue("la suite PHPUnit pasa");

  await page.getByTestId("task-criteria-save").click();
  await expect.poll(() => puts.length).toBe(1);
  expect(puts[0].acceptance_criteria).toEqual([
    "composer audit sin vulnerabilidades",
    "la suite PHPUnit pasa",
  ]);
});

test("regenerating an existing task shows a comparison and accepting applies it", async ({
  page,
}) => {
  const puts: { acceptance_criteria: unknown[] }[] = [];
  await setup(page, {
    initialCriteria: ["criterio viejo"],
    proposal: ["criterio nuevo A", "criterio nuevo B"],
    onPut: (b) => puts.push(b),
  });
  await openSheet(page);

  await page.getByTestId("task-criteria-generate").click();

  // Comparison modal shows both the current and the proposed criteria.
  const modal = page.getByTestId("task-criteria-compare");
  await expect(modal).toBeVisible();
  await expect(modal.getByText("criterio viejo")).toBeVisible();
  await expect(modal.getByText("criterio nuevo A")).toBeVisible();

  await page.getByTestId("task-criteria-compare-accept").click();
  await expect(modal).toHaveCount(0);

  // Editor preloaded with the proposal; save persists it.
  await expect(
    page.getByTestId("task-criterion-row-0").getByTestId("task-criterion-input"),
  ).toHaveValue("criterio nuevo A");
  await page.getByTestId("task-criteria-save").click();
  await expect.poll(() => puts.length).toBe(1);
  expect(puts[0].acceptance_criteria).toEqual(["criterio nuevo A", "criterio nuevo B"]);
});

test("cancelling the comparison keeps the existing criteria and does not save", async ({
  page,
}) => {
  const puts: { acceptance_criteria: unknown[] }[] = [];
  await setup(page, {
    initialCriteria: ["criterio viejo"],
    proposal: ["criterio nuevo A"],
    onPut: (b) => puts.push(b),
  });
  await openSheet(page);

  await page.getByTestId("task-criteria-generate").click();
  await expect(page.getByTestId("task-criteria-compare")).toBeVisible();
  await page.getByTestId("task-criteria-compare-cancel").click();

  await expect(page.getByTestId("task-criteria-compare")).toHaveCount(0);
  // Still in read mode with the original criterion; no PUT happened.
  await expect(page.getByTestId("task-detail-criteria").getByText("criterio viejo")).toBeVisible();
  await expect(page.getByTestId("task-criteria-save")).toHaveCount(0);
  expect(puts.length).toBe(0);
});

test("a generation error surfaces a message and does not touch the criteria", async ({ page }) => {
  await setup(page, { initialCriteria: [], generateStatus: 409 });
  await openSheet(page);

  await page.getByTestId("task-criteria-generate").click();
  await expect(page.getByTestId("task-criteria-generate-error")).toBeVisible();
  await expect(page.getByTestId("task-criteria-compare")).toHaveCount(0);
  // Not switched to edit mode.
  await expect(page.getByTestId("task-criteria-save")).toHaveCount(0);
});

test("Escape closes only the comparison modal, keeping the task sheet open", async ({ page }) => {
  await setup(page, { initialCriteria: ["criterio viejo"], proposal: ["criterio nuevo"] });
  await openSheet(page);

  await page.getByTestId("task-criteria-generate").click();
  await expect(page.getByTestId("task-criteria-compare")).toBeVisible();

  // Escape must dismiss ONLY the topmost dialog (the comparison), not the sheet.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("task-criteria-compare")).toHaveCount(0);
  await expect(page.getByTestId("task-detail-sheet")).toBeVisible();
  // Nothing was overwritten: still read mode with the original criterion.
  await expect(page.getByTestId("task-detail-criteria").getByText("criterio viejo")).toBeVisible();
});
