import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

/**
 * E2E for the kanban real-time WebSocket (task_02_21).
 *
 * The board opens `/ws/kanban/{project_id}` for the selected plan. A
 * `task.status_changed` frame moves the card to the new column with no
 * refresh. The WebSocket is mocked with `routeWebSocket`.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const TASK_ID = "10000000-0000-0000-0000-000000000001";

async function setupBoard(page: Page): Promise<{ socket: () => WebSocketRoute }> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/projects", (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: PROJECT_ID,
          name: "Plan demo",
          description: null,
          status: "active",
          team_id: null,
          is_template: false,
        },
      ]),
    });
  });
  await page.route("**/teams", (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/projects/${PROJECT_ID}/tasks`, (route) => {
    if (route.request().method() !== "GET") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: TASK_ID,
          project_id: PROJECT_ID,
          plan_id: null,
          title: "Diseñar esquema",
          description: null,
          status: "backlog",
          priority: "high",
          assigned_agent_id: null,
        },
      ]),
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

  await expect(page.getByTestId(`plan-card-${PROJECT_ID}`)).toBeVisible();
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
