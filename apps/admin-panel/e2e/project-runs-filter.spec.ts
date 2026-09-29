import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E de la pestaña Runs del proyecto (`task_ui_12`, plan ui-reestructuracion-2026-09-09):
 * filtrar los runs por plan. Subset mockeado (sin backend): `GET /runs` responde según el
 * `plan_id` que reciba, así que el test falla si el panel no lo manda.
 *
 * OJO: en Playwright gana la ruta registrada DESPUÉS, por eso el comodín va primero.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const PROJECT_ID = "8f14e45f-ceea-467a-9f0a-1a2b3c4d5e6f";
const BASE = `/admin/projects/${PROJECT_ID}`;

const TENANT_ADMIN = {
  user_id: "aaaa0000-0000-0000-0000-000000000001",
  email: "admin@a.test",
  full_name: "Admin A",
  is_system_admin: false,
  is_system_owner: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const PROJECT = {
  id: PROJECT_ID,
  name: "Web",
  description: "Proyecto de prueba",
  status: "active",
  team_id: null,
  is_template: false,
  model_config: {},
  chat_model_config: {},
  git_config: null,
  worker_config: {},
  repository_config: {},
};

const PLANS = [
  { id: "plan-a", title: "Migrar login" },
  { id: "plan-b", title: "Rediseñar checkout" },
];

function run(id: string, planId: string, planTitle: string, taskTitle: string) {
  return {
    id,
    created_at: "2026-09-29T10:00:00Z",
    task_id: `t-${id}`,
    task_title: taskTitle,
    plan_id: planId,
    plan_title: planTitle,
    agent_id: "ag1",
    agent_name: "Backend",
    agent_role: "backend_dev",
    model: "m-a",
    verdict: "done",
    succeeded: true,
    finish_status: null,
    retry_count: 0,
    duration_ms: 1200,
    total_tokens: 100,
    total_cost_usd: "0.5",
    started_at: null,
    completed_at: null,
    display_currency: null,
    display_cost: null,
    applied_rate: null,
    applied_rate_date: null,
  };
}

const RUNS = [
  run("r1", "plan-a", "Migrar login", "Tarea del login"),
  run("r2", "plan-b", "Rediseñar checkout", "Tarea del checkout"),
];

async function mockApi(page: Page): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/me", (route) => route.fulfill(json(TENANT_ADMIN)));
  // Comodín primero: lo demás del proyecto sólo necesita cargar.
  await page.route(apiRoute(`/projects/${PROJECT_ID}/**`), (r) => r.fulfill(json([])));
  await page.route(apiRoute(`/projects/${PROJECT_ID}`), (r) => r.fulfill(json(PROJECT)));
  await page.route(apiRoute(`/projects/${PROJECT_ID}/plans`), (r) => r.fulfill(json(PLANS)));
  await page.route(apiRoute("/agents"), (r) => r.fulfill(json([{ id: "ag1", name: "Backend" }])));
  await page.route(apiRoute("/teams"), (r) => r.fulfill(json([])));
  await page.route(apiRoute("/runs**"), (route) => {
    const url = new URL(route.request().url());
    // El mock sólo devuelve los runs del proyecto pedido y, si llega, del plan pedido.
    if (url.searchParams.get("project_id") !== PROJECT_ID) return route.fulfill(json([]));
    const plan = url.searchParams.get("plan_id");
    return route.fulfill(json(plan ? RUNS.filter((r) => r.plan_id === plan) : RUNS));
  });
}

test("la pestaña Runs filtra los runs del proyecto por plan", async ({ page }) => {
  await mockApi(page);
  await page.goto(`${BASE}/runs`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("nav-project-runs")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("run-row-r1")).toContainText("Tarea del login");
  await expect(page.getByTestId("run-row-r2")).toContainText("Tarea del checkout");

  await page.getByTestId("project-runs-plan-filter").selectOption("plan-a");
  await expect(page.getByTestId("run-row-r1")).toBeVisible();
  await expect(page.getByTestId("run-row-r2")).toHaveCount(0);

  await page.getByTestId("project-runs-plan-filter").selectOption("");
  await expect(page.getByTestId("run-row-r2")).toBeVisible();
});
