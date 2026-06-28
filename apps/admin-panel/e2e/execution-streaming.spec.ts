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

// F47: the worker republishes a step wrapped as `payload={"step":{…}}` (the
// index lives at payload.step.index, NOT at the payload root) — this mirrors
// the real on-the-wire shape so the test would catch a regression of the bug
// where the UI read `"index" in payload` and discarded every live step.
function stepFrame(index: number, summary: string) {
  return JSON.stringify({
    type: "step",
    occurred_at: "2026-05-22T10:00:00Z",
    payload: {
      step: {
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
    },
  });
}

// F48: terminal frames carry no `index`. `execution.finished` ships the final
// result; the UI must refetch the execution to reflect the terminal state.
function finishedFrame() {
  return JSON.stringify({
    type: "execution.finished",
    occurred_at: "2026-05-22T10:01:00Z",
    payload: {
      result: { status: "done", output: "the result", finish_status: "success", iterations: 2 },
    },
  });
}

async function setup(
  page: Page,
  body: () => unknown = () => EMPTY_EXECUTION,
): Promise<{ socket: () => WebSocketRoute }> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  // Match the api-server origin exactly — a `**/executions/...` glob
  // would also catch the `/admin/executions/...` page navigation. The body is
  // computed per request so a test can flip the persisted state mid-run.
  await page.route(`http://localhost:8001/executions/${EXEC_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body()),
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

test("an execution.finished frame refetches and shows the terminal state", async ({ page }) => {
  // The persisted execution starts `running`; once the run finalises the
  // backend flips it to `done` with its output. The finished frame must
  // trigger a refetch that surfaces that terminal state.
  let finalized = false;
  const { socket } = await setup(page, () =>
    finalized
      ? { ...EMPTY_EXECUTION, status: "done", output: "the result", finish_status: "success" }
      : EMPTY_EXECUTION,
  );
  await page.goto(`/admin/executions/${EXEC_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("execution-status")).toContainText("En curso");

  finalized = true;
  socket().send(finishedFrame());

  await expect(page.getByTestId("execution-status")).toContainText("Completado");
  await expect(page.getByTestId("execution-output")).toContainText("the result");
});
