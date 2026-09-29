import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E de la bandeja humana unificada (task_ui_03): aprobar una solicitud desde
 * la pestaña Aprobaciones de /admin/inbox. Resolver hace
 * `POST /approvals/{id}/resolve` y la tarjeta sale de la lista; el contador de la
 * pestaña baja a 0. Las tres rutas antiguas redirigen a su pestaña.
 *
 * Todas las llamadas del panel se interceptan con `page.route` (suite mockeada).
 */

const REQUEST = {
  id: "aa000000-0000-0000-0000-000000000003",
  execution_id: "ee000000-0000-0000-0000-000000000001",
  task_id: "22222222-2222-2222-2222-222222222222",
  project_id: "33333333-3333-3333-3333-333333333333",
  category: "production_deploy",
  action: { tool: "shell_exec", args: { command: "deploy.sh" } },
  status: "pending",
  requested_at: "2026-09-29T10:00:00Z",
};

interface ResolveCapture {
  calls: number;
  lastBody: { approved?: boolean; reason?: string | null };
}

const json = (body: unknown) => ({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(body),
});

async function setup(page: Page): Promise<ResolveCapture> {
  const capture: ResolveCapture = { calls: 0, lastBody: {} };
  let resolved = false;

  await seedSession(page);

  await page.route("http://localhost:8001/approvals", (route) =>
    route.fulfill(json(resolved ? [] : [REQUEST])),
  );
  await page.route(`http://localhost:8001/approvals/${REQUEST.id}/resolve`, (route) => {
    if (route.request().method() !== "POST") return route.continue();
    capture.calls += 1;
    capture.lastBody = JSON.parse(route.request().postData() ?? "{}");
    resolved = true;
    return route.fulfill(json({ ...REQUEST, status: "approved" }));
  });
  // Las otras fuentes de la bandeja, vacías.
  await page.route("http://localhost:8001/human-queue", (route) => route.fulfill(json([])));
  await page.route("http://localhost:8001/inbox/reviews**", (route) => route.fulfill(json([])));
  await page.route("http://localhost:8001/inbox/assignments", (route) => route.fulfill(json([])));
  await page.route("http://localhost:8001/notifications/logs**", (route) =>
    route.fulfill(json({ items: [], total: 0, unread: 0, limit: 1, offset: 0 })),
  );

  return capture;
}

test("aprobar una solicitud desde la bandeja hace POST y vacía la pestaña", async ({ page }) => {
  const capture = await setup(page);
  await page.goto("/admin/inbox?tab=approvals", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("inbox-count-approvals")).toHaveText("1");
  await expect(page.getByTestId(`approval-card-${REQUEST.id}`)).toBeVisible();
  await page.getByTestId(`approve-${REQUEST.id}`).click();

  await expect.poll(() => capture.calls).toBe(1);
  expect(capture.lastBody.approved).toBe(true);

  await expect(page.getByTestId(`approval-card-${REQUEST.id}`)).toHaveCount(0);
  await expect(page.getByTestId("approvals-empty")).toBeVisible();
  await expect(page.getByTestId("inbox-count-approvals")).toHaveText("0");
});

test("/admin/approvals redirige a la pestaña Aprobaciones", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/approvals", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/admin\/inbox\?tab=approvals/);
  await expect(page.getByTestId(`approval-card-${REQUEST.id}`)).toBeVisible();
});
