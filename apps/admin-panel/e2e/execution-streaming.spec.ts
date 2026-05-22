import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

/**
 * E2E for execution streaming over WebSocket (task_02_20).
 *
 * The timeline opens `/ws/executions/{id}` and appends every streamed
 * step event to the timeline live. The WebSocket is mocked with
 * `routeWebSocket`; the test pushes step frames and asserts they land.
 */

const EXEC_ID = "ee000000-0000-0000-0000-000000000002";

// An execution whose run has not produced any steps yet — everything
// shown will arrive over the WebSocket.
const EMPTY_EXECUTION = {
  id: EXEC_ID,
  tenant_id: "11111111-1111-1111-1111-111111111111",
  task_id: "22222222-2222-2222-2222-222222222222",
  agent_id: null,
  status: "running",
  abort_code: null,
  output: null,
  iterations: 0,
  total_tokens: 0,
  total_cost_usd: 0,
  tool_call_count: 0,
  model_call_count: 0,
  steps_log: [],
};

function stepFrame(index: number, summary: string) {
  return JSON.stringify({
    type: "step",
    occurred_at: "2026-05-22T10:00:00Z",
    payload: {
      index,
      kind: "model_call",
      node: "plan",
      status: "ok",
      summary,
      model: "claude-test",
      tokens_in: 60,
      tokens_out: 20,
      total_tokens: 80,
      cost_usd: 0.001,
    },
  });
}

async function setup(page: Page): Promise<{ socket: () => WebSocketRoute }> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  // Match the api-server origin exactly — a `**/executions/...` glob
  // would also catch the `/admin/executions/...` page navigation.
  await page.route(`http://localhost:8001/executions/${EXEC_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EMPTY_EXECUTION),
    }),
  );
  let captured: WebSocketRoute | undefined;
  await page.routeWebSocket(/\/ws\/executions\//, (ws) => {
    captured = ws;
  });
  return {
    socket: () => {
      if (!captured) throw new Error("WebSocket was never opened by the page");
      return captured;
    },
  };
}

test("the timeline starts empty and is wired to the WebSocket", async ({ page }) => {
  const { socket } = await setup(page);
  await page.goto(`/admin/executions/${EXEC_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("execution-timeline")).toBeVisible();
  await expect(page.getByTestId("timeline-empty")).toBeVisible();
  // The page has opened the live socket.
  await expect.poll(() => socket()).toBeDefined();
});

test("streamed step events append to the timeline live", async ({ page }) => {
  const { socket } = await setup(page);
  await page.goto(`/admin/executions/${EXEC_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("execution-timeline")).toBeVisible();

  socket().send(stepFrame(0, "live planning turn"));
  await expect(page.getByTestId("timeline-step-0")).toBeVisible();
  await expect(page.getByTestId("timeline-step-0")).toContainText("live planning turn");

  socket().send(stepFrame(1, "second planning turn"));
  await expect(page.getByTestId("timeline-step-1")).toBeVisible();

  // The empty-state is gone and the live counter reflects both frames.
  await expect(page.getByTestId("timeline-empty")).toHaveCount(0);
  await expect(page.getByTestId("timeline-live-count")).toContainText("2");
});
