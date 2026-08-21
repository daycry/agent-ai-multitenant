import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the personal "Histórico" tab — past tasks + performance metrics
 * (Plan 16 task_16_10).
 *
 * From the personal inbox (/admin/inbox), switching to the "Histórico" tab
 * shows the caller's:
 *   - performance metrics (GET /inbox/metrics): mean acceptance time, mean
 *     execution time, first-try approval rate, mean logged hours;
 *   - past delivered tasks (GET /inbox/history): closed work sessions with the
 *     task / project / plan context, hours and output note.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET /me                — a plain TENANT USER
 *   - GET /inbox/assignments — one active assignment (the "Activas" tab)
 *   - GET /inbox/metrics     — seeded metrics
 *   - GET /inbox/history     — two past deliveries
 *
 * NOTE: WRITTEN but NOT run as part of task_16_10 (the plan's automated check for
 * this task is python-pytest — see tests/integration/test_human_metrics.py).
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/human-history.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";

const TENANT_USER = {
  user_id: "aaaa0000-0000-0000-0000-000000000001",
  email: "alice@a.test",
  full_name: "Alice",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_user", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const ACTIVE_ASSIGNMENT = {
  assignment_id: "asg-active-1",
  task_id: "task-active-1",
  human_agent_id: "ha-1",
  assignment_status: "pending_acceptance",
  task_status: "assigned_to_human",
  assigned_at: new Date().toISOString(),
  acceptance_deadline: new Date(Date.now() + 6 * 3600 * 1000).toISOString(),
  task_title: "Revisión legal pendiente",
  task_description: null,
  project_id: "proj-1",
  project_name: "Proyecto A",
  plan_id: "plan-1",
  plan_title: "Plan de cierre",
};

const METRICS = {
  tasks_worked: 2,
  work_sessions_completed: 3,
  assignments_accepted: 2,
  mean_acceptance_time_seconds: 7200, // 2 h
  mean_execution_time_seconds: 14400, // 4 h
  first_try_approval_rate: 0.5,
  mean_hours_logged: 3.0,
};

const HISTORY = [
  {
    work_session_id: "ws-2",
    task_id: "task-two",
    task_title: "Decisión de marca",
    task_status: "done",
    project_id: "proj-1",
    project_name: "Proyecto A",
    plan_id: "plan-1",
    plan_title: "Plan A",
    start_at: "2026-05-01T14:00:00Z",
    end_at: "2026-05-01T20:00:00Z",
    hours_logged: null,
    comments: "Reintento tras rechazo del revisor.",
    attachments_count: 0,
  },
  {
    work_session_id: "ws-1",
    task_id: "task-one",
    task_title: "Revisión legal del contrato",
    task_status: "done",
    project_id: "proj-1",
    project_name: "Proyecto A",
    plan_id: "plan-1",
    plan_title: "Plan A",
    start_at: "2026-05-01T09:00:00Z",
    end_at: "2026-05-01T11:00:00Z",
    hours_logged: "2.00",
    comments: "Cláusulas 3 y 7 OK.",
    attachments_count: 2,
  },
];

async function setup(page: Page): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_USER),
    }),
  );

  await page.route("**/inbox/metrics", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(METRICS),
    }),
  );

  await page.route("**/inbox/history", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(HISTORY),
    }),
  );

  // The bare assignments list (the "Activas" tab). Keep AFTER the more specific
  // /metrics + /history routes so those win.
  await page.route("**/inbox/assignments", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([ACTIVE_ASSIGNMENT]),
    }),
  );
}

test("the Histórico tab shows per-user metrics and past tasks", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/inbox");

  // The active tab is shown first.
  await expect(page.getByTestId("inbox-tab-history")).toBeVisible();

  // Switch to the Histórico tab.
  await page.getByTestId("inbox-tab-history").click();

  // Metrics tiles render with the seeded, human-formatted values.
  await expect(page.getByTestId("metrics-grid")).toBeVisible();
  await expect(page.getByTestId("metric-acceptance")).toContainText("2 h");
  await expect(page.getByTestId("metric-execution")).toContainText("4 h");
  await expect(page.getByTestId("metric-first-try")).toContainText("50 %");
  await expect(page.getByTestId("metric-hours")).toContainText("3.0 h");

  // Past tasks render, newest first (the redo of task_two on top).
  await expect(page.getByTestId("history-grid")).toBeVisible();
  await expect(page.getByTestId("history-entry-ws-2")).toContainText("Decisión de marca");
  await expect(page.getByTestId("history-entry-ws-1")).toContainText("Revisión legal del contrato");
  await expect(page.getByTestId("history-hours-ws-1")).toContainText("2.00 h");
});

test("empty history shows a friendly empty state and 'sin datos' metrics", async ({ page }) => {
  await seedSession(page, { tenantId: TENANT_ID });
  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_USER),
    }),
  );
  await page.route("**/inbox/metrics", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        tasks_worked: 0,
        work_sessions_completed: 0,
        assignments_accepted: 0,
        mean_acceptance_time_seconds: null,
        mean_execution_time_seconds: null,
        first_try_approval_rate: null,
        mean_hours_logged: null,
      }),
    }),
  );
  await page.route("**/inbox/history", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/inbox/assignments", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  await page.goto("/admin/inbox");
  await page.getByTestId("inbox-tab-history").click();

  await expect(page.getByTestId("metric-acceptance")).toContainText("Sin datos aún");
  await expect(page.getByTestId("history-empty")).toBeVisible();
});
