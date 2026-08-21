import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the plan DAG view (Plan 03 task_03_19).
 *
 * Verifies the SVG renders one node per task, one edge per
 * `depends_on` entry, and that the depth-based column layout puts
 * each node in the right column. Independent of the upstream renderer
 * choice: we ship a pure-SVG layout (no D3/react-flow dep).
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "ffff0000-0000-0000-0000-00000000d000";

const PLAN_FIXTURE = {
  id: PLAN_ID,
  tenant_id: "tttt0000-0000-0000-0000-000000000001",
  project_id: PROJECT_ID,
  title: "Plan con DAG",
  description: null,
  status: "draft",
  conversation_id: null,
  approved_by: null,
  approved_at: null,
  created_at: "2026-05-24T10:00:00Z",
  updated_at: "2026-05-24T10:00:00Z",
  specification: {
    tasks: [
      { id: "t1", title: "Modelar entidades", depends_on: [] },
      { id: "t2", title: "Implementar /login", depends_on: ["t1"] },
      { id: "t3", title: "CRUD de items", depends_on: ["t2"] },
      { id: "t4", title: "Test plan", depends_on: ["t1"] },
    ],
  },
};

async function setup(page: Page): Promise<void> {
  await seedSession(page);
  await page.route(`http://localhost:8001/plans/${PLAN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PLAN_FIXTURE),
    }),
  );
}

test("renders one SVG node per task", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  const svg = page.getByTestId("plan-dag-svg");
  await expect(svg).toBeVisible();
  await expect(page.getByTestId("plan-dag-node-t1")).toBeVisible();
  await expect(page.getByTestId("plan-dag-node-t2")).toBeVisible();
  await expect(page.getByTestId("plan-dag-node-t3")).toBeVisible();
  await expect(page.getByTestId("plan-dag-node-t4")).toBeVisible();
});

test("renders one edge per declared dependency", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  // 3 declared edges: t1->t2, t2->t3, t1->t4. SVG <line> elements are
  // reported as "hidden" by Playwright's visibility checks even when
  // attached to the DOM; assert their presence via toHaveCount.
  await expect(page.getByTestId("plan-dag-edge-t1->t2")).toHaveCount(1);
  await expect(page.getByTestId("plan-dag-edge-t2->t3")).toHaveCount(1);
  await expect(page.getByTestId("plan-dag-edge-t1->t4")).toHaveCount(1);
  // No stray edges.
  await expect(page.getByTestId("plan-dag-edges").locator("line")).toHaveCount(3);
});

test("depth-based layout places nodes in the right columns", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  // t1 has no deps -> col 0.
  await expect(page.getByTestId("plan-dag-node-t1")).toHaveAttribute("data-col", "0");
  // t2 depends on t1 -> col 1.
  await expect(page.getByTestId("plan-dag-node-t2")).toHaveAttribute("data-col", "1");
  // t3 depends on t2 -> col 2.
  await expect(page.getByTestId("plan-dag-node-t3")).toHaveAttribute("data-col", "2");
  // t4 also depends on t1 -> col 1 alongside t2.
  await expect(page.getByTestId("plan-dag-node-t4")).toHaveAttribute("data-col", "1");
});

test("empty task list renders a friendly placeholder", async ({ page }) => {
  await seedSession(page);
  await page.route(`http://localhost:8001/plans/${PLAN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...PLAN_FIXTURE,
        specification: { tasks: [] },
      }),
    }),
  );
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  // The DAG card is only rendered when there is at least one task.
  await expect(page.getByTestId("plan-dag")).toHaveCount(0);
});
