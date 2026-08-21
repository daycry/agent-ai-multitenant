import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the kanban real-time WebSocket (task_02_21).
 *
 * The board opens `/ws/kanban/{project_id}` for the selected plan. A
 * `task.status_changed` frame moves the card to the new column with no
 * refresh. The WebSocket is mocked with `routeWebSocket`.
 *
 * Reparado el 2026-08-19 (subset mockeado de CI). El spec seguía el modelo
 * ANTERIOR al doble Kanban: la fila de arriba eran PROYECTOS y las tareas se
 * pedían sin filtrar. Hoy (CLAUDE.md §6 y c8/T11) arriba van los PLANES
 * (`GET /plans`) y abajo SOLO las tareas del plan seleccionado
 * (`/projects/{id}/tasks?plan_id=...`), así que:
 *
 *   - hay que mockear `/plans`, que el spec ni pedía: sin planes no hay
 *     selección, y sin selección no hay tablero de tareas ni WebSocket;
 *   - `plan-card-{id}` lleva el id del PLAN, no el del proyecto;
 *   - las tres listas se piden paginadas (`?limit=&offset=`), y los patrones sin
 *     query no casaban.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "20000000-0000-0000-0000-000000000001";
const TASK_ID = "10000000-0000-0000-0000-000000000001";

const PROJECT = {
  id: PROJECT_ID,
  name: "Proyecto demo",
  description: null,
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

const TASK = {
  id: TASK_ID,
  project_id: PROJECT_ID,
  plan_id: PLAN_ID,
  title: "Diseñar esquema",
  description: null,
  status: "backlog",
  priority: "high",
  assigned_agent_id: null,
  depends_on: [],
};

async function setupBoard(page: Page): Promise<{ socket: () => WebSocketRoute }> {
  await seedSession(page);
  // Las tres listas van paginadas (`fetchAllPages` añade `?limit=&offset=`), de
  // ahí el `?*` en los patrones: sin él el mock no casaba con la petición real.
  await page.route(apiRoute("/projects?*"), (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([PROJECT]),
    });
  });
  await page.route(apiRoute("/plans?*"), (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([PLAN]),
    });
  });
  await page.route(apiRoute(`/projects/${PROJECT_ID}/tasks?*`), (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([TASK]),
    });
  });

  let captured: WebSocketRoute | undefined;
  await page.routeWebSocket(/\/ws\/kanban\//, (ws) => {
    captured = ws;
  });
  return {
    socket: () => {
      if (!captured) throw new Error("kanban WebSocket was never opened by the board");
      return captured;
    },
  };
}

test("the board wires up a live kanban socket for the selected plan", async ({ page }) => {
  const { socket } = await setupBoard(page);
  await page.goto("/admin/board");

  await expect(page.getByTestId(`plan-card-${PLAN_ID}`)).toBeVisible();
  await expect(page.getByTestId("board-live-indicator")).toBeVisible();
  await expect
    .poll(() => {
      try {
        socket();
        return true;
      } catch {
        return false;
      }
    })
    .toBe(true);
});

test("a task.status_changed frame moves the card to its new column", async ({ page }) => {
  const { socket } = await setupBoard(page);
  await page.goto("/admin/board");

  await expect(page.getByTestId("col-backlog").getByTestId(`task-card-${TASK_ID}`)).toBeVisible();

  socket().send(
    JSON.stringify({
      type: "task.status_changed",
      task_id: TASK_ID,
      project_id: PROJECT_ID,
      payload: { old_status: "backlog", new_status: "in_progress" },
    }),
  );

  await expect(
    page.getByTestId("col-in_progress").getByTestId(`task-card-${TASK_ID}`),
  ).toBeVisible();
  await expect(page.getByTestId("col-backlog").getByTestId(`task-card-${TASK_ID}`)).toHaveCount(0);
});
