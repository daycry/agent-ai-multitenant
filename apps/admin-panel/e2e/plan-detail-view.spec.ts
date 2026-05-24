import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the plan detail page that renders the canonical template
 * (Plan 03 task_03_18).
 *
 * Loads a plan with a richly populated specification and asserts that:
 *   - cabecera shows the title + status badge,
 *   - summary section renders scope / decisions / risks,
 *   - estimates section formats the costs and effort,
 *   - phases render as an ordered list with the task ids cited,
 *   - tasks table lists each task row with deps.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "ffff0000-0000-0000-0000-000000000abc";

const FULL_PLAN = {
  id: PLAN_ID,
  tenant_id: "tttt0000-0000-0000-0000-000000000001",
  project_id: PROJECT_ID,
  title: "API de inventario con auth",
  description: "Primer MVP de la API REST",
  status: "pending_approval",
  conversation_id: null,
  approved_by: null,
  approved_at: null,
  created_at: "2026-05-24T10:00:00Z",
  updated_at: "2026-05-24T10:00:00Z",
  specification: {
    summary: {
      title: "API de inventario con auth",
      description: "MVP de la API REST con JWT y persistencia.",
      scope_in: ["Registro", "Login", "CRUD de items"],
      scope_out: ["Mobile", "ML"],
      decisions: ["PostgreSQL en lugar de MongoDB"],
      risks: [{ name: "JWT mal rotado", mitigation: "Rotación semanal" }, "Indexación tardía"],
    },
    phases: [
      { name: "Auth", description: "JWT y sesiones", tasks: ["t1", "t2"] },
      { name: "Inventario", description: "CRUD de items", tasks: ["t3"] },
    ],
    tasks: [
      {
        id: "t1",
        title: "Modelar usuarios",
        complexity: "m",
        role: "backend_dev",
        depends_on: [],
      },
      {
        id: "t2",
        title: "Implementar /login",
        complexity: "m",
        role: "backend_dev",
        depends_on: ["t1"],
      },
      {
        id: "t3",
        title: "CRUD de items",
        complexity: "l",
        role: "backend_dev",
        depends_on: ["t2"],
      },
    ],
    estimates: {
      duration_calendar: "3 semanas",
      effort_person_days: 12,
      cost_human_eur: [4800, 6000],
      cost_ai_eur: [40, 80],
    },
    metadata: { template_version: "1.0" },
  },
};

async function setup(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`http://localhost:8001/plans/${PLAN_ID}`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FULL_PLAN),
    });
  });
}

test("header shows title and status badge", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("plan-detail-header")).toContainText("API de inventario con auth");
  const badge = page.getByTestId("plan-detail-status-badge");
  await expect(badge).toBeVisible();
  await expect(badge).toHaveAttribute("data-status", "pending_approval");
  await expect(badge).toContainText("Pendiente");
});

test("summary section renders scope, decisions and risks", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("plan-scope-in")).toContainText("Registro");
  await expect(page.getByTestId("plan-scope-out")).toContainText("Mobile");
  await expect(page.getByTestId("plan-decisions")).toContainText("PostgreSQL");
  const risks = page.getByTestId("plan-risks");
  await expect(risks).toContainText("JWT mal rotado");
  await expect(risks).toContainText("Rotación semanal");
  await expect(risks).toContainText("Indexación tardía");
});

test("estimates section renders duration, effort and cost ranges", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("estimate-duration")).toContainText("3 semanas");
  await expect(page.getByTestId("estimate-effort")).toContainText("12");
  // Node may ship without the es-ES ICU data so toLocaleString may or
  // may not insert thousand separators. Match the digits + the EUR
  // suffix without assuming the separator.
  await expect(page.getByTestId("estimate-cost-human")).toContainText("€");
  await expect(page.getByTestId("estimate-cost-human")).toContainText("–");
  const humanText = await page.getByTestId("estimate-cost-human").innerText();
  expect(humanText.replace(/[.,\s]/g, "")).toContain("4800");
  expect(humanText.replace(/[.,\s]/g, "")).toContain("6000");
  await expect(page.getByTestId("estimate-cost-ai")).toContainText("40");
});

test("phases list with their task ids and task table with deps", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  const phasesCard = page.getByTestId("plan-phases");
  await expect(phasesCard).toContainText("Auth");
  await expect(phasesCard).toContainText("Inventario");
  await expect(page.getByTestId("plan-phase-0")).toContainText("t1");
  await expect(page.getByTestId("plan-phase-0")).toContainText("t2");

  // Tasks table: each row visible with its dependencies.
  const t2 = page.getByTestId("plan-task-t2");
  await expect(t2).toContainText("Implementar /login");
  await expect(t2).toContainText("backend_dev");
  await expect(t2).toContainText("t1");
  const t1 = page.getByTestId("plan-task-t1");
  await expect(t1).toContainText("Modelar usuarios");
  // t1 has no deps -> shows "—".
  await expect(t1).toContainText("—");
});

test("back link points to the project plans list", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-detail-back")).toHaveAttribute(
    "href",
    `/admin/projects/${PROJECT_ID}/plans`,
  );
});

test("empty specification renders a friendly placeholder", async ({ page }) => {
  const empty = { ...FULL_PLAN, id: PLAN_ID, specification: {} };
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`http://localhost:8001/plans/${PLAN_ID}`, (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(empty),
    });
  });
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("plan-summary-empty")).toBeVisible();
  // No phases / tasks / estimates cards when spec is empty.
  await expect(page.getByTestId("plan-phases")).toHaveCount(0);
  await expect(page.getByTestId("plan-tasks")).toHaveCount(0);
  await expect(page.getByTestId("plan-estimates")).toHaveCount(0);
});
