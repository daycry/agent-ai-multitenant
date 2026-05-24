import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the Gantt view with critical path (Plan 03 task_03_20).
 *
 * Verifies the SVG renders one row per task, computes earliest_start
 * / earliest_end correctly, and flags the critical path with the
 * `data-critical="true"` attribute. The summary line cites the
 * total project duration.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "ffff0000-0000-0000-0000-00000000ga11";

const PLAN_FIXTURE = {
  id: PLAN_ID,
  tenant_id: "tttt0000-0000-0000-0000-000000000001",
  project_id: PROJECT_ID,
  title: "Plan con Gantt",
  description: null,
  status: "draft",
  conversation_id: null,
  approved_by: null,
  approved_at: null,
  created_at: "2026-05-24T10:00:00Z",
  updated_at: "2026-05-24T10:00:00Z",
  specification: {
    // Diamond:
    //   t1 (4h) ─┬─ t2 (8h) ─┐
    //            └─ t3 (2h) ─┴─ t4 (3h)
    // Critical path: t1 -> t2 -> t4 = 15h.
    // t3 has slack (4..6 vs 4..12 latest), not critical.
    tasks: [
      { id: "t1", title: "Modelar", estimated_hours: 4, depends_on: [] },
      { id: "t2", title: "Implementar", estimated_hours: 8, depends_on: ["t1"] },
      { id: "t3", title: "Tests", estimated_hours: 2, depends_on: ["t1"] },
      { id: "t4", title: "Deploy", estimated_hours: 3, depends_on: ["t2", "t3"] },
    ],
  },
};

async function setup(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`http://localhost:8001/plans/${PLAN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PLAN_FIXTURE),
    }),
  );
}

test("renders one Gantt row per task with the right earliest_start/end", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("plan-gantt-svg")).toHaveCount(1);

  const t1 = page.getByTestId("plan-gantt-row-t1");
  await expect(t1).toHaveAttribute("data-earliest-start", "0");
  await expect(t1).toHaveAttribute("data-earliest-end", "4");

  const t2 = page.getByTestId("plan-gantt-row-t2");
  await expect(t2).toHaveAttribute("data-earliest-start", "4");
  await expect(t2).toHaveAttribute("data-earliest-end", "12");

  const t3 = page.getByTestId("plan-gantt-row-t3");
  await expect(t3).toHaveAttribute("data-earliest-start", "4");
  await expect(t3).toHaveAttribute("data-earliest-end", "6");

  const t4 = page.getByTestId("plan-gantt-row-t4");
  // t4 waits for max(t2, t3) = max(12, 6) = 12.
  await expect(t4).toHaveAttribute("data-earliest-start", "12");
  await expect(t4).toHaveAttribute("data-earliest-end", "15");
});

test("critical path flags t1 -> t2 -> t4 and excludes t3", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("plan-gantt-row-t1")).toHaveAttribute("data-critical", "true");
  await expect(page.getByTestId("plan-gantt-row-t2")).toHaveAttribute("data-critical", "true");
  await expect(page.getByTestId("plan-gantt-row-t4")).toHaveAttribute("data-critical", "true");
  // t3 has 6 hours of slack (latest_start=10 vs earliest_start=4).
  await expect(page.getByTestId("plan-gantt-row-t3")).toHaveAttribute("data-critical", "false");
  await expect(page.getByTestId("plan-gantt-row-t3")).toHaveAttribute("data-slack", "6");
});

test("summary cites the total project duration", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  const summary = page.getByTestId("plan-gantt-summary");
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("15h");
  await expect(summary).toContainText("línea crítica");
});
