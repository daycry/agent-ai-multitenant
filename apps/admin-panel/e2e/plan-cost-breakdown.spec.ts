import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the cost breakdown table on the plan detail page
 * (Plan 03 task_03_24).
 *
 * The page fetches /plans/{id}/cost-breakdown and renders two tables:
 *   - Coste humano (currency + hourly rate + per-task hours + total)
 *   - Coste IA (range min/max, per-task model + complexity)
 *
 * Missing models in the catalog surface as a destructive-toned warning.
 */

const PROJECT_ID = "00000000-0000-0000-0000-000000000001";
const PLAN_ID = "ffff0000-0000-0000-0000-00000000cb01";

const PLAN_FIXTURE = {
  id: PLAN_ID,
  tenant_id: "tttt0000-0000-0000-0000-000000000001",
  project_id: PROJECT_ID,
  title: "Plan con coste",
  description: null,
  status: "draft",
  conversation_id: null,
  approved_by: null,
  approved_at: null,
  created_at: "2026-05-25T10:00:00Z",
  updated_at: "2026-05-25T10:00:00Z",
  specification: {
    tasks: [
      { id: "t1", title: "Modelar", complexity: "m", estimated_hours: 4 },
      { id: "t2", title: "Implementar", complexity: "l", estimated_hours: 12 },
    ],
  },
};

const COST_FIXTURE = {
  human: {
    currency: "EUR",
    hourly_rate: "50.00",
    total_hours: "16.000",
    total_cost: "800.00",
    tasks: [
      { task_id: "t1", title: "Modelar", hours: "4.000", cost: "200.00" },
      { task_id: "t2", title: "Implementar", hours: "12.000", cost: "600.00" },
    ],
  },
  ai: {
    currency: "USD",
    default_model_id: "gpt-4o",
    cost_min: "0.1500",
    cost_max: "0.4500",
    tasks: [
      {
        task_id: "t1",
        title: "Modelar",
        complexity: "m",
        model_id: "gpt-4o",
        tokens_in_min: 7500,
        tokens_in_max: 22500,
        tokens_out_min: 2000,
        tokens_out_max: 6000,
        cost_min: "0.0388",
        cost_max: "0.1163",
      },
      {
        task_id: "t2",
        title: "Implementar",
        complexity: "l",
        model_id: "gpt-4o",
        tokens_in_min: 20000,
        tokens_in_max: 60000,
        tokens_out_min: 5000,
        tokens_out_max: 15000,
        cost_min: "0.1000",
        cost_max: "0.3000",
      },
    ],
    missing_models: [] as string[],
  },
};

async function setup(page: Page, costOverride: typeof COST_FIXTURE | null = null): Promise<void> {
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
  await page.route(`http://localhost:8001/plans/${PLAN_ID}/cost-breakdown*`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(costOverride ?? COST_FIXTURE),
    }),
  );
  // Mock the comments list too — the detail page fetches it.
  await page.route(`http://localhost:8001/plans/${PLAN_ID}/comments`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    }),
  );
}

test("human cost table renders rows + total", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  const card = page.getByTestId("plan-cost-breakdown");
  await expect(card).toBeVisible();

  const human = page.getByTestId("plan-cost-human");
  await expect(human).toContainText("EUR");
  await expect(human).toContainText("50.00 EUR/h");
  await expect(page.getByTestId("plan-cost-human-row-t1")).toContainText("4.000");
  await expect(page.getByTestId("plan-cost-human-row-t1")).toContainText("200.00");
  await expect(page.getByTestId("plan-cost-human-row-t2")).toContainText("600.00");
  await expect(page.getByTestId("plan-cost-human-total")).toContainText("800.00 EUR");
  await expect(page.getByTestId("plan-cost-human-total-hours")).toContainText("16.000");
});

test("AI cost table renders the range + default model", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });

  const ai = page.getByTestId("plan-cost-ai");
  await expect(ai).toContainText("USD");
  await expect(ai).toContainText("gpt-4o");

  const t1 = page.getByTestId("plan-cost-ai-row-t1");
  await expect(t1).toContainText("0.0388 USD");
  await expect(t1).toContainText("0.1163 USD");
  await expect(t1).toContainText("m");
  await expect(page.getByTestId("plan-cost-ai-row-t2")).toContainText("0.3000 USD");

  await expect(page.getByTestId("plan-cost-ai-total-min")).toContainText("0.1500 USD");
  await expect(page.getByTestId("plan-cost-ai-total-max")).toContainText("0.4500 USD");

  // No missing-models warning when the catalog is complete.
  await expect(page.getByTestId("plan-cost-ai-missing-models")).toHaveCount(0);
});

test("missing models in the catalog show a destructive warning", async ({ page }) => {
  const withMissing = {
    ...COST_FIXTURE,
    ai: { ...COST_FIXTURE.ai, missing_models: ["phantom-9", "ghost-1"] },
  };
  await setup(page, withMissing);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  const warn = page.getByTestId("plan-cost-ai-missing-models");
  await expect(warn).toBeVisible();
  await expect(warn).toContainText("phantom-9");
  await expect(warn).toContainText("ghost-1");
});

test("empty cost breakdown shows the friendly empty-state", async ({ page }) => {
  const emptyCost = {
    human: {
      currency: "EUR",
      hourly_rate: "50.00",
      total_hours: "0.000",
      total_cost: "0.00",
      tasks: [],
    },
    ai: {
      currency: "USD",
      default_model_id: "gpt-4o",
      cost_min: "0.0000",
      cost_max: "0.0000",
      tasks: [],
      missing_models: [],
    },
  };
  await setup(page, emptyCost);
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-cost-breakdown-empty")).toBeVisible();
});
