import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the Doble Kanban (task_01_22).
 *
 * Doctrine: doubled view — Plans gerencial on top, Tasks operativa
 * below, filtered by the selected plan. The board uses native HTML5
 * drag & drop (no extra dep).
 *
 * Visible run:
 *   .\scripts\dev\run-e2e.ps1 -Headed -SlowMo 600 -Spec e2e\dual-kanban.spec.ts
 *
 * Scope of this spec:
 *   1. Nav item navigates to /admin/board.
 *   2. With no tenant-owned projects (the wizard test never submits
 *      because login doesn't issue a `tid` claim yet), the Plans
 *      section shows its empty state and the Tasks section says
 *      "no selection".
 *   3. The 7 status columns are present in the expected order when a
 *      plan is selected (covered with a route mock so we don't depend
 *      on database-side seed of tenant projects).
 *   4. Drag & drop status change: simulated end-to-end via DataTransfer
 *      events against a mocked `/projects` + `/tasks` response, with
 *      the PUT call intercepted so no tid-claim write actually hits
 *      the API.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);
}

test("nav-tablero opens the board and shows empty state for tenants with no plans", async ({
  page,
}) => {
  // The superadmin's portfolio view (BYPASSRLS) sees every project
  // across every tenant, so a persistent dev DB will almost always
  // have non-empty /projects. Mock it to [] to assert the empty-
  // state UX in isolation.
  await page.route("**/projects", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  await login(page);
  await page.getByTestId("nav-board").click();
  await expect(page).toHaveURL(/\/admin\/board$/);

  await expect(page.getByTestId("plans-empty")).toBeVisible();
  await expect(page.getByTestId("board-no-selection")).toBeVisible();
});

test("board renders the 7 status columns with a selected plan", async ({ page }) => {
  await login(page);

  // Mock the projects + tasks endpoints so the board has a plan to
  // drive without depending on a write path that requires tid.
  await page.route("**/projects", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "00000000-0000-0000-0000-000000000001",
          name: "Plan demo",
          description: "Mock plan for the board test.",
          status: "active",
          team_id: null,
          is_template: false,
        },
      ]),
    });
  });
  await page.route("**/projects/00000000-0000-0000-0000-000000000001/tasks", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "10000000-0000-0000-0000-000000000001",
          project_id: "00000000-0000-0000-0000-000000000001",
          plan_id: null,
          title: "Diseñar esquema",
          description: null,
          status: "backlog",
          priority: "high",
          assigned_agent_id: null,
        },
        {
          id: "10000000-0000-0000-0000-000000000002",
          project_id: "00000000-0000-0000-0000-000000000001",
          plan_id: null,
          title: "Migración inicial",
          description: null,
          status: "in_progress",
          priority: "medium",
          assigned_agent_id: null,
        },
      ]),
    });
  });

  await page.goto("/admin/board");
  await expect(page.getByTestId("plans-grid")).toBeVisible();
  await expect(page.getByTestId("plan-card-00000000-0000-0000-0000-000000000001")).toBeVisible();

  // 7 columns in the expected order. The Tasks board uses one
  // container per column with testid `col-{status}`.
  const expectedOrder = [
    "backlog",
    "ready",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "cancelled",
  ];
  const columns = page.getByTestId("board-columns").locator("[data-status]");
  await expect(columns).toHaveCount(7);
  for (let i = 0; i < expectedOrder.length; i += 1) {
    await expect(columns.nth(i)).toHaveAttribute("data-status", expectedOrder[i]);
  }

  // Task cards land in the right columns based on their seed status.
  await expect(
    page.getByTestId("col-backlog").getByTestId("task-card-10000000-0000-0000-0000-000000000001"),
  ).toBeVisible();
  await expect(
    page
      .getByTestId("col-in_progress")
      .getByTestId("task-card-10000000-0000-0000-0000-000000000002"),
  ).toBeVisible();
});

test("drag a card to another column triggers PUT and updates UI optimistically", async ({
  page,
}) => {
  await login(page);

  // Mock GETs as in the previous test.
  await page.route("**/projects", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "00000000-0000-0000-0000-000000000001",
          name: "Plan demo",
          description: null,
          status: "active",
          team_id: null,
          is_template: false,
        },
      ]),
    });
  });
  await page.route("**/projects/00000000-0000-0000-0000-000000000001/tasks", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "10000000-0000-0000-0000-000000000001",
          project_id: "00000000-0000-0000-0000-000000000001",
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

  // Intercept the PUT so we can verify it without needing a tid claim.
  let putCalls = 0;
  let putBody: unknown = null;
  await page.route(
    "**/projects/00000000-0000-0000-0000-000000000001/tasks/10000000-0000-0000-0000-000000000001",
    async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      putCalls += 1;
      putBody = JSON.parse(route.request().postData() ?? "null");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "10000000-0000-0000-0000-000000000001",
          project_id: "00000000-0000-0000-0000-000000000001",
          plan_id: null,
          title: "Diseñar esquema",
          description: null,
          status: "ready",
          priority: "high",
          assigned_agent_id: null,
        }),
      });
    },
  );

  await page.goto("/admin/board");
  const source = page.getByTestId("task-card-10000000-0000-0000-0000-000000000001");
  await expect(source).toBeVisible();
  const target = page.getByTestId("col-ready");
  await expect(target).toBeVisible();

  // Simulate HTML5 drag & drop. Playwright's `dragTo` issues real
  // dragstart/dragover/drop with a shared DataTransfer.
  await source.dragTo(target);

  // The PUT happened with status=ready, and the card now lives in the
  // ready column thanks to the optimistic update.
  await expect.poll(() => putCalls, { timeout: 5_000 }).toBeGreaterThanOrEqual(1);
  expect(putBody).toMatchObject({ status: "ready" });
  await expect(
    page.getByTestId("col-ready").getByTestId("task-card-10000000-0000-0000-0000-000000000001"),
  ).toBeVisible();
});
