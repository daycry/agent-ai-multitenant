import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Sincronizar al Kanban" dialog on the plan detail page
 * (Plan 03 task_03_27).
 *
 * Mocks the plan detail + sync endpoints. Drives:
 *   - opening the dialog,
 *   - the three scope options (Total / Por fase / Selección),
 *   - submission and the result line shown afterwards.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "ffff0000-0000-0000-0000-00000000ab01";

const PLAN_FIXTURE = {
  id: PLAN_ID,
  tenant_id: "tttt0000-0000-0000-0000-000000000001",
  project_id: PROJECT_ID,
  title: "Plan sincronizable",
  description: null,
  status: "approved",
  conversation_id: null,
  approved_by: null,
  approved_at: null,
  first_approved_by: null,
  first_approved_at: null,
  created_at: "2026-05-25T10:00:00Z",
  updated_at: "2026-05-25T10:00:00Z",
  specification: {
    phases: [
      { name: "Diseño", tasks: ["t1", "t2"] },
      { name: "Build", tasks: ["t3"] },
    ],
    tasks: [
      { id: "t1", title: "Modelar", complexity: "m" },
      { id: "t2", title: "API", complexity: "m", depends_on: ["t1"] },
      { id: "t3", title: "Backend", complexity: "l", depends_on: ["t2"] },
    ],
  },
};

interface SyncCapture {
  calls: number;
  lastBody: { scope?: string; phase_index?: number; task_ids?: string[] };
}

async function setup(page: Page): Promise<SyncCapture> {
  const capture: SyncCapture = { calls: 0, lastBody: {} };

  await seedSession(page);
  await page.route(`http://localhost:8001/plans/${PLAN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PLAN_FIXTURE),
    }),
  );
  // Cost breakdown and comments are queried by the page on mount.
  await page.route(`http://localhost:8001/plans/${PLAN_ID}/cost-breakdown*`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        human: {
          currency: "EUR",
          hourly_rate: "50.00",
          total_hours: "0.000",
          total_cost: "0.00",
          tasks: [],
        },
        ai: {
          currency: "USD",
          default_model_id: "gpt-4o",
          cost_min: "0.0000",
          cost_max: "0.0000",
          tasks: [],
          missing_models: [],
        },
      }),
    }),
  );
  await page.route(`http://localhost:8001/plans/${PLAN_ID}/comments`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`http://localhost:8001/plans/${PLAN_ID}/sync-to-kanban`, (route) => {
    capture.calls += 1;
    const body = JSON.parse(route.request().postData() ?? "{}");
    capture.lastBody = body;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        created_task_ids: { t1: "11111111-0000-0000-0000-000000000001" },
        skipped_task_ids: {},
        dependencies_created: 0,
      }),
    });
  });
  return capture;
}

test("plan detail shows the sync button and a friendly description", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  const card = page.getByTestId("plan-sync-to-kanban");
  await expect(card).toBeVisible();
  await expect(page.getByTestId("plan-sync-open")).toBeEnabled();
});

test("total scope is the default and POSTs scope=total", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("plan-sync-open").click();
  await expect(page.getByTestId("plan-sync-dialog")).toBeVisible();
  await expect(page.getByTestId("plan-sync-scope-total")).toBeChecked();

  await page.getByTestId("plan-sync-confirm").click();
  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.scope).toBe("total");
  expect(capture.lastBody.phase_index).toBeUndefined();
  expect(capture.lastBody.task_ids).toBeUndefined();
  await expect(page.getByTestId("plan-sync-result")).toBeVisible();
});

test("phase scope sends phase_index", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("plan-sync-open").click();
  await page.getByTestId("plan-sync-scope-phase").check();
  await page.getByTestId("plan-sync-phase-select").selectOption({ index: 1 });
  await page.getByTestId("plan-sync-confirm").click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.scope).toBe("phase");
  expect(capture.lastBody.phase_index).toBe(1);
});

test("selection scope sends the picked task ids and disables confirm when empty", async ({
  page,
}) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("plan-sync-open").click();
  await page.getByTestId("plan-sync-scope-selection").check();
  // No task picked yet → confirm is disabled.
  await expect(page.getByTestId("plan-sync-confirm")).toBeDisabled();

  await page.getByTestId("plan-sync-selection-t1").check();
  await page.getByTestId("plan-sync-selection-t3").check();
  await expect(page.getByTestId("plan-sync-confirm")).toBeEnabled();

  await page.getByTestId("plan-sync-confirm").click();
  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.scope).toBe("selection");
  expect(capture.lastBody.task_ids).toEqual(["t1", "t3"]);
});
