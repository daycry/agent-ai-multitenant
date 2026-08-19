import { expect, test, type Page, type Route } from "@playwright/test";

import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the Doble Kanban (task_01_22).
 *
 * Doctrine (CLAUDE.md §6): doubled view — PLANS on top (gerencial), TASKS of the
 * selected plan below (operativa). Never a flat board mixing tasks from several
 * plans. The board uses native HTML5 drag & drop (no extra dep).
 *
 * Scope of this spec:
 *   1. The nav item lands on /admin/board.
 *   2. With no plans, the Plans section shows its empty state and the Tasks
 *      section says "no selection".
 *   3. The 8 status columns are present, in order, with a plan selected.
 *   4. Drag & drop status change end-to-end: DataTransfer events, the PUT
 *      intercepted, and the optimistic move asserted.
 *
 * ---------------------------------------------------------------------------
 * 2026-08-19 — REESCRITO. Dos motivos, y ninguno es "bajar el listón":
 *
 *   a) **Hacía LOGIN REAL** contra el api-server. CI lo barre igualmente dentro
 *      del subset "mockeado" (usa `page.route`), y allí no hay backend: los tres
 *      tests morían en `toHaveURL(/admin/dashboard)` sin llegar a ver el
 *      tablero. Ahora siembra la sesión con `seedSession` (ADR 0133), que es la
 *      migración que este spec nunca recibió.
 *   b) **Seguía el modelo viejo**: la fila de arriba eran PROYECTOS y las tareas
 *      se pedían sin filtrar por plan. Desde c8/T11 arriba van los PLANES
 *      (`GET /plans`) y abajo sólo las del plan seleccionado
 *      (`/projects/{id}/tasks?plan_id=…`), y las tres listas se piden paginadas
 *      (`?limit=&offset=`), así que los patrones sin query tampoco casaban.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "20000000-0000-0000-0000-000000000001";
const TASK_BACKLOG = "10000000-0000-0000-0000-000000000001";
const TASK_IN_PROGRESS = "10000000-0000-0000-0000-000000000002";

const PROJECT = {
  id: PROJECT_ID,
  name: "Proyecto demo",
  description: "Mock project for the board test.",
  status: "active",
  team_id: null,
  is_template: false,
};

const PLAN = {
  id: PLAN_ID,
  project_id: PROJECT_ID,
  title: "Plan demo",
  status: "in_progress",
};

function task(id: string, title: string, status: string, priority: string) {
  return {
    id,
    project_id: PROJECT_ID,
    plan_id: PLAN_ID,
    title,
    description: null,
    status,
    priority,
    assigned_agent_id: null,
    depends_on: [],
  };
}

interface BoardOptions {
  /** Planes que devuelve `GET /plans` (vacío ⇒ estado vacío del tablero). */
  plans?: object[];
  /** Tareas del plan seleccionado. */
  tasks?: object[];
  /** Captura del PUT de cambio de estado. */
  onPut?: (body: unknown) => void;
}

async function setupBoard(page: Page, opts: BoardOptions = {}): Promise<void> {
  await seedSession(page);
  const plans = opts.plans ?? [PLAN];
  const tasks = opts.tasks ?? [];

  const json = (route: Route, body: unknown) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

  await page.route(apiRoute("/projects?*"), (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return json(route, plans.length > 0 ? [PROJECT] : []);
  });
  await page.route(apiRoute("/plans?*"), (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return json(route, plans);
  });
  await page.route(apiRoute(`/projects/${PROJECT_ID}/tasks?*`), (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return json(route, tasks);
  });
  await page.route(apiRoute(`/projects/${PROJECT_ID}/tasks/${TASK_BACKLOG}`), async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    const body = JSON.parse(route.request().postData() ?? "null");
    opts.onPut?.(body);
    return json(route, { ...task(TASK_BACKLOG, "Diseñar esquema", "ready", "high") });
  });
}

test("nav-tablero opens the board and shows empty state for tenants with no plans", async ({
  page,
}) => {
  await setupBoard(page, { plans: [] });
  await page.goto("/admin/dashboard", { waitUntil: "domcontentloaded" });

  await page.getByTestId("nav-board").click();
  await expect(page).toHaveURL(/\/admin\/board$/);

  await expect(page.getByTestId("plans-empty")).toBeVisible();
  await expect(page.getByTestId("board-no-selection")).toBeVisible();
});

test("board renders the 8 status columns with a selected plan", async ({ page }) => {
  await setupBoard(page, {
    tasks: [
      task(TASK_BACKLOG, "Diseñar esquema", "backlog", "high"),
      task(TASK_IN_PROGRESS, "Migración inicial", "in_progress", "medium"),
    ],
  });
  await page.goto("/admin/board", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("plans-grid")).toBeVisible();
  // La tarjeta de arriba es un PLAN, no un proyecto (doble Kanban, §6).
  await expect(page.getByTestId(`plan-card-${PLAN_ID}`)).toBeVisible();

  // 8 columnas en su orden. El tablero pinta un contenedor por columna con
  // testid `col-{status}`.
  //
  // Eran 7 hasta que el ADR 0020 metió `awaiting_human_approval` entre "En
  // curso" y "Revisión": mientras un humano aprueba, el agente queda LIBRE, y
  // esa espera necesita columna propia o la tarea se lee como "en curso" sin
  // que nadie esté trabajando en ella. El spec seguía contando 7 (2026-08-19).
  const expectedOrder = [
    "backlog",
    "ready",
    "in_progress",
    "awaiting_human_approval",
    "in_review",
    "blocked",
    "done",
    "cancelled",
  ];
  const columns = page.getByTestId("board-columns").locator("[data-status]");
  await expect(columns).toHaveCount(expectedOrder.length);
  for (let i = 0; i < expectedOrder.length; i += 1) {
    await expect(columns.nth(i)).toHaveAttribute("data-status", expectedOrder[i]);
  }

  // Task cards land in the right columns based on their status.
  await expect(
    page.getByTestId("col-backlog").getByTestId(`task-card-${TASK_BACKLOG}`),
  ).toBeVisible();
  await expect(
    page.getByTestId("col-in_progress").getByTestId(`task-card-${TASK_IN_PROGRESS}`),
  ).toBeVisible();
});

test("drag a card to another column triggers PUT and updates UI optimistically", async ({
  page,
}) => {
  let putBody: unknown = null;
  let putCalls = 0;
  await setupBoard(page, {
    tasks: [task(TASK_BACKLOG, "Diseñar esquema", "backlog", "high")],
    onPut: (body) => {
      putCalls += 1;
      putBody = body;
    },
  });
  await page.goto("/admin/board", { waitUntil: "domcontentloaded" });

  const source = page.getByTestId(`task-card-${TASK_BACKLOG}`);
  await expect(source).toBeVisible();
  const target = page.getByTestId("col-ready");
  await expect(target).toBeVisible();

  // Simulate HTML5 drag & drop. Playwright's `dragTo` issues real
  // dragstart/dragover/drop with a shared DataTransfer.
  await source.dragTo(target);

  // The PUT happened with status=ready, and the card now lives in the
  // ready column thanks to the optimistic update.
  await expect.poll(() => putCalls, { timeout: 5_000 }).toBeGreaterThanOrEqual(1);
  expect(putBody).toMatchObject({ status: "ready" });
  await expect(
    page.getByTestId("col-ready").getByTestId(`task-card-${TASK_BACKLOG}`),
  ).toBeVisible();
});
