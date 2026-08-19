import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the escalated-tasks panel (Plan 06 task_06_34b3).
 *
 * Mocks /plans/{id}/escalated-tasks + /tasks/{id}/human-action y comprueba:
 * 1) una fila por tarea escalada, 2) los cuatro botones, 3) que pulsar uno
 * llama al endpoint.
 *
 * Reparado el 2026-08-19: los mocks apuntaban a `/api/plans/...` y `lib/api.ts`
 * pide `http://localhost:8001/plans/...`. No casaba ninguno, así que la pantalla
 * hablaba con un backend inexistente y no pintaba ni el estado vacío.
 */

const PLAN_ID = "plan-esc-1";

async function setup(
  page: Page,
  opts: { tasks?: object[]; onAction?: (req: object) => void } = {},
): Promise<void> {
  const tasks = opts.tasks ?? [];
  const onAction = opts.onAction;
  await seedSession(page);
  // Migas de pan del plan (la cabecera de la página lo pide).
  await page.route(apiRoute(`/plans/${PLAN_ID}`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: PLAN_ID,
        project_id: "proj-1",
        title: "Plan escalado",
        status: "in_progress",
      }),
    }),
  );
  await page.route(apiRoute(`/plans/${PLAN_ID}/escalated-tasks`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tasks }),
    }),
  );
  await page.route(apiRoute(`/tasks/*/human-action`), async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    onAction?.(body);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    });
  });
}

const SAMPLE = {
  id: "task-1",
  title: "Implementar webhook",
  description: "Endpoint rechaza el POST con 500",
  retry_count: 3,
  history: [],
};

test("empty state when no escalated tasks", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("escalated-empty")).toBeVisible();
});

test("renders one row per escalated task with four action buttons", async ({ page }) => {
  await setup(page, { tasks: [SAMPLE] });
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("escalated-task-1")).toBeVisible();
  await expect(page.getByTestId("approve-task-1")).toBeVisible();
  await expect(page.getByTestId("reassign-task-1")).toBeVisible();
  await expect(page.getByTestId("block-task-1")).toBeVisible();
  await expect(page.getByTestId("cancel-task-1")).toBeVisible();
});

test("clicking approve calls the endpoint with action=approve_manual", async ({ page }) => {
  const calls: object[] = [];
  await setup(page, { tasks: [SAMPLE], onAction: (req) => calls.push(req) });
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });
  // La fila la pinta la query mockeada: esperarla garantiza que el cliente ya
  // está hidratado y que el click llega a React, no al HTML servido.
  await expect(page.getByTestId("escalated-task-1")).toBeVisible();
  await page.getByTestId("approve-task-1").click();
  await page.waitForTimeout(100);
  expect(calls[0]).toMatchObject({ action: "approve_manual" });
});
