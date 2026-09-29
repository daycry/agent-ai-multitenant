import { expect, test, type Page } from "@playwright/test";

import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E del dashboard del tenant (`task_ui_02`, plan ui-reestructuracion-2026-09-09).
 *
 * Subset mockeado: `GET /tenant/dashboard` sale de un fixture y la salud del stack
 * responde 403, que es lo que ve un miembro sin permiso de System Admin. Comprueba lo
 * que el test unitario no puede: que la ruta real `/admin/dashboard` monta las seis
 * secciones en el navegador, que la salud «sólo cuando duele» no rompe la página sin
 * permiso, y que un pendiente lleva a la pantalla donde se resuelve.
 */

const DASHBOARD = {
  pending: {
    total: 2,
    by_kind: { plan_validation: 1, approval_request: 1 },
    oldest: [
      {
        kind: "plan_validation",
        id: "plan-1",
        title: "Plan esperando validación",
        project_name: "Web",
        age_seconds: 7200,
        url_path: "/admin/projects/proj-1/plans/plan-1",
      },
    ],
  },
  projects: [
    {
      project_id: "proj-1",
      project_name: "Web",
      active_plans: 1,
      tasks_done: 2,
      tasks_total: 4,
      running: 1,
      last_failure: null,
      url_path: "/admin/projects/proj-1",
    },
  ],
  spend: { available: false, reason: "no_budget_configured" },
  issues: [],
  activity: [
    {
      execution_id: "exec-1",
      status: "running",
      task_title: "Maquetar la portada",
      project_name: "Web",
      agent_name: "Frontend Dev",
      at: "2026-09-29T10:00:00Z",
      url_path: "/admin/executions/exec-1",
    },
  ],
  mcp: [],
  generated_at: "2026-09-29T10:00:00Z",
};

async function mockDashboard(page: Page): Promise<void> {
  await page.route(apiRoute("/tenant/dashboard"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DASHBOARD),
    }),
  );
  await page.route(apiRoute("/admin/system-health"), (route) =>
    route.fulfill({ status: 403, contentType: "application/json", body: '{"detail":"forbidden"}' }),
  );
}

test("el dashboard monta sus secciones y enlaza cada pendiente con su pantalla", async ({
  page,
}) => {
  await seedSession(page);
  await mockDashboard(page);
  await page.goto("/admin/dashboard");

  await expect(page.getByTestId("dashboard-grid")).toBeVisible();
  for (const id of [
    "dashboard-pending",
    "dashboard-projects",
    "dashboard-spend",
    "dashboard-activity",
    "dashboard-mcp",
  ]) {
    await expect(page.getByTestId(id)).toBeVisible();
  }
  // Sin avisos y sin permiso para la salud del stack: la sección de atención no aparece
  // y la página no enseña un error.
  await expect(page.getByTestId("dashboard-issues")).toHaveCount(0);
  await expect(page.getByTestId("dashboard-error")).toHaveCount(0);

  const pending = page.getByRole("link", { name: "Plan esperando validación" });
  await expect(pending).toHaveAttribute("href", "/admin/projects/proj-1/plans/plan-1");
  await expect(page.getByRole("link", { name: "Maquetar la portada" })).toHaveAttribute(
    "href",
    "/admin/executions/exec-1",
  );
});
