import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for plan → escalated/review deep links (Plan 06.6 task_06_6_12).
 */

const PROJECT_ID = "proj-link-1";
const PLAN_ID = "plan-link-1";

function planFixture(status: string): object {
  return {
    id: PLAN_ID,
    project_id: PROJECT_ID,
    title: "Plan con deep links",
    description: "para testear los links",
    status,
    specification: {
      summary: "summary",
      phases: [],
      tasks: [],
      estimates: null,
    },
    created_at: "2026-05-27T12:00:00Z",
    updated_at: "2026-05-27T12:00:00Z",
  };
}

async function setup(page: Page, planStatus: string): Promise<void> {
  await seedSession(page);
  await page.route(apiRoute(`/plans/${PLAN_ID}`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(planFixture(planStatus)),
    }),
  );
  // El desglose de coste (`GET /plans/{id}/cost-breakdown`) se mockeaba con un
  // `{}`, un payload que ese endpoint no devuelve nunca: la sección hace
  // `const { human, ai } = data` y `human.tasks.length` reventaba, el
  // `AdminErrorBoundary` sustituía la PÁGINA ENTERA por "Algo ha fallado" y el
  // test se quedaba sin enlaces que mirar. El fallo aparecía en el segundo
  // `expect` del test, no en el primero, y por eso parecía "el link no existe".
  // El mock ahora responde un desglose VACÍO pero bien formado (2026-08-19).
  await page.route(apiRoute(`/plans/${PLAN_ID}/cost*`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        human: {
          currency: "EUR",
          hourly_rate: "0.00",
          total_hours: "0.00",
          total_cost: "0.00",
          tasks: [],
        },
        ai: {
          currency: "USD",
          default_model_id: "claude-opus-4",
          cost_min: "0.00",
          cost_max: "0.00",
          tasks: [],
          missing_models: [],
        },
      }),
    }),
  );
}

test("plan detail shows escalated tasks link always", async ({ page }) => {
  await setup(page, "in_progress");
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-deep-links")).toBeVisible();
  await expect(page.getByTestId("plan-link-escalated")).toHaveAttribute(
    "href",
    `/admin/plans/${PLAN_ID}/escalated`,
  );
});

test("plan in pending_human_validation also shows review session link", async ({ page }) => {
  await setup(page, "pending_human_validation");
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-link-review")).toBeVisible();
});

test("plan not in pending_human_validation hides review session link", async ({ page }) => {
  await setup(page, "in_progress");
  await page.goto(`/admin/projects/${PROJECT_ID}/plans/${PLAN_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("plan-link-review")).toHaveCount(0);
});
