import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E de la navegación entre pestañas del proyecto (`task_ui_10`, plan
 * ui-reestructuracion-2026-09-09): el Resumen en la raíz, la barra contextual con
 * sus ocho entradas y el paso Resumen → Capacidades → Conocimiento → Resumen sin
 * perder la barra del proyecto.
 *
 * Subset mockeado (sin backend): `page.route` con `apiRoute`, que ata el patrón al
 * origen del api-server para no interceptar la navegación del propio panel.
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

const OVERVIEW = {
  project: { id: PROJECT_ID, name: "Web", status: "active", team_id: null },
  plans: [
    {
      plan_id: "pl1",
      title: "Migrar login",
      status: "in_progress",
      tasks_done: 1,
      tasks_total: 4,
      url_path: `${BASE}/plans/pl1`,
    },
  ],
  runs: { running: 1, last_failure: null, url_path: `${BASE}/tasks` },
  spend: { available: false, reason: "no_budget_configured" },
  warnings: [
    { kind: "mcp_no_tools", count: 1, names: ["atlassian"], url_path: `${BASE}/mcp-servers` },
  ],
  activity: [],
  generated_at: "2026-09-29T10:00:00+00:00",
};

async function mockApi(page: Page): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });
  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_ADMIN),
    }),
  );
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  // Playwright da prioridad a la ruta registrada DESPUÉS: el comodín va primero para
  // que no tape `/overview` ni la ficha.
  // Las pantallas de destino sólo necesitan cargar: listas vacías.
  await page.route(apiRoute(`/projects/${PROJECT_ID}/**`), (r) => r.fulfill(json([])));
  // Dos endpoints de la ficha que devuelven un objeto, no una lista: un `[]` rompe
  // su render. 404 es lo que ve un proyecto sin preview ni capacidades compuestas.
  const notFound = { status: 404, contentType: "application/json", body: '{"detail":"not found"}' };
  await page.route(apiRoute(`/projects/${PROJECT_ID}/capabilities`), (r) => r.fulfill(notFound));
  await page.route(apiRoute(`/projects/${PROJECT_ID}/preview-session`), (r) => r.fulfill(notFound));
  await page.route(apiRoute(`/projects/${PROJECT_ID}/overview`), (r) => r.fulfill(json(OVERVIEW)));
  await page.route(apiRoute(`/projects/${PROJECT_ID}`), (r) => r.fulfill(json(PROJECT)));
  await page.route(apiRoute("/teams"), (r) => r.fulfill(json([])));
}

test("el Resumen es la raíz del proyecto y enseña avisos, planes y runs", async ({ page }) => {
  await mockApi(page);
  await page.goto(BASE, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("sidebar-nav")).toHaveAttribute("data-area", "project");
  await expect(page.getByTestId("overview-plans")).toContainText("Migrar login");
  await expect(page.getByTestId("overview-warning-mcp_no_tools")).toHaveAttribute(
    "href",
    `${BASE}/mcp-servers`,
  );
  await expect(page.getByTestId("nav-project-overview")).toHaveAttribute("aria-current", "page");
});

test("la barra tiene las ocho entradas y las ocho se enlazan", async ({ page }) => {
  await mockApi(page);
  await page.goto(BASE, { waitUntil: "domcontentloaded" });

  const nav = page.getByTestId("sidebar-nav");
  for (const id of [
    "overview",
    "board",
    "runs",
    "team",
    "capabilities",
    "knowledge",
    "costs",
    "settings",
  ]) {
    await expect(nav.getByTestId(`nav-project-${id}`)).toBeVisible();
  }
  // `task_ui_12`: Runs, Equipo y Costes ya tienen pantalla, así que se enlazan.
  for (const id of ["runs", "team", "costs"]) {
    await expect(nav.getByTestId(`nav-project-${id}`)).toHaveAttribute("href", `${BASE}/${id}`);
    await expect(nav.getByTestId(`nav-project-${id}`)).not.toHaveAttribute("aria-disabled", "true");
  }
});

test("navega Resumen → Capacidades → Conocimiento → Resumen dentro del proyecto", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  const nav = page.getByTestId("sidebar-nav");

  await nav.getByTestId("nav-project-capabilities").click();
  await expect(page).toHaveURL(`${BASE}/capabilities`);
  await expect(nav).toHaveAttribute("data-area", "project");
  await expect(nav.getByTestId("nav-project-capabilities")).toHaveAttribute("aria-current", "page");

  await nav.getByTestId("nav-project-knowledge").click();
  await expect(page).toHaveURL(`${BASE}/knowledge-bases`);
  await expect(nav.getByTestId("nav-project-knowledge")).toHaveAttribute("aria-current", "page");

  await nav.getByTestId("nav-project-overview").click();
  await expect(page).toHaveURL(BASE);
  await expect(nav.getByTestId("nav-project-overview")).toHaveAttribute("aria-current", "page");

  // «← Proyectos» devuelve la barra del tenant.
  await nav.getByTestId("nav-back-to-projects").click();
  await expect(page).toHaveURL("/admin/projects");
  await expect(page.getByTestId("sidebar-nav")).toHaveAttribute("data-area", "work");
});

test("un bloque del Resumen lleva a su pestaña", async ({ page }) => {
  await mockApi(page);
  await page.goto(BASE, { waitUntil: "domcontentloaded" });

  await page.getByTestId("overview-warning-mcp_no_tools").click();
  await expect(page).toHaveURL(`${BASE}/mcp-servers`);
});
