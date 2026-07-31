import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the Execution Timeline (task_02_22).
 *
 * The timeline renders an execution's steps_log: a row per step, each
 * expandable to its detail, with the cost and timing shown per step.
 *
 * Self-contained: the auth token is injected straight into
 * localStorage (the admin layout's client-side gate just checks it),
 * and the REST + WebSocket calls are mocked — no api-server needed.
 */

const EXEC_ID = "ee000000-0000-0000-0000-000000000001";

const EXECUTION = {
  id: EXEC_ID,
  tenant_id: "11111111-1111-1111-1111-111111111111",
  task_id: "22222222-2222-2222-2222-222222222222",
  agent_id: null,
  status: "done",
  abort_code: null,
  output: "the sea poem",
  iterations: 1,
  total_tokens: 130,
  total_cost_usd: 0.0025,
  tool_call_count: 1,
  model_call_count: 1,
  steps_log: [
    {
      index: 0,
      kind: "node",
      node: "perceive",
      status: "ok",
      summary: "Perceived task: Write a sea poem",
      started_at: "2026-05-22T10:00:00Z",
      ended_at: "2026-05-22T10:00:00Z",
    },
    {
      index: 1,
      kind: "memory_read",
      node: "recall",
      status: "ok",
      summary: "Recalled 0 memory items",
      placeholder: true,
    },
    {
      index: 2,
      kind: "model_call",
      node: "plan",
      status: "ok",
      summary: "decided: act",
      model: "claude-test",
      tokens_in: 100,
      tokens_out: 30,
      total_tokens: 130,
      cost_usd: 0.0025,
      started_at: "2026-05-22T10:00:01Z",
      ended_at: "2026-05-22T10:00:03Z",
    },
    {
      index: 3,
      kind: "tool_call",
      node: "act",
      status: "ok",
      summary: "Tool 'echo' -> ok",
      tool: "echo",
      args: { text: "hi" },
      result: { ok: true, output: "hi" },
    },
  ],
};

async function setup(page: Page) {
  await seedSession(page);
  // Match the api-server origin exactly — a `**/executions/...` glob
  // would also catch the `/admin/executions/...` page navigation.
  await page.route(`http://localhost:8001/executions/${EXEC_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EXECUTION),
    }),
  );
  // The page also opens a live WebSocket — mock it (no events here).
  await page.routeWebSocket(/\/ws\/executions\//, () => {});
}

test("timeline renders one row per step with the summary card", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/executions/${EXEC_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("execution-timeline")).toBeVisible();
  for (let i = 0; i < 4; i += 1) {
    await expect(page.getByTestId(`timeline-step-${i}`)).toBeVisible();
  }

  // The status badge renders the Spanish label for the execution status (F50).
  await expect(page.getByTestId("execution-status")).toContainText("Completado");
  await expect(page.getByTestId("execution-iterations")).toHaveText("1");
  await expect(page.getByTestId("execution-cost")).toContainText("$0.0025");
});

test("a step expands to reveal its detail", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/executions/${EXEC_ID}`, { waitUntil: "domcontentloaded" });

  // Collapsed by default — the detail panel is not in the DOM.
  await expect(page.getByTestId("step-detail-2")).toHaveCount(0);

  await page.getByTestId("step-toggle-2").click();
  const detail = page.getByTestId("step-detail-2");
  await expect(detail).toBeVisible();
  await expect(detail).toContainText("claude-test");

  // The tool_call step shows its args + result when expanded.
  await page.getByTestId("step-toggle-3").click();
  await expect(page.getByTestId("step-detail-3")).toContainText("echo");
});

test("cost and duration are shown per step", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/executions/${EXEC_ID}`, { waitUntil: "domcontentloaded" });

  // The model_call step ran 10:00:01 -> 10:00:03 = 2 s, cost $0.0025.
  await expect(page.getByTestId("step-cost-2")).toContainText("$0.0025");
  await expect(page.getByTestId("step-duration-2")).toContainText("s");
});
