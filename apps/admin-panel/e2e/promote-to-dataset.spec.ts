import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for the "Promote to dataset" action on the execution detail page
 * (Plan 14 task_14_02).
 *
 * A tenant_admin promotes a real APPROVED task (its approved execution) into a
 * per-tenant golden dataset as a dataset item. The action button is wrapped in
 * <RoleGuard min="tenant_admin">; the dialog lets the operator pick an existing
 * dataset or create one inline. Promotion is idempotent — re-promoting the same
 * task returns the existing item (created=false).
 *
 * Mocks:
 *   - GET  /me                              — identity (admin vs plain user)
 *   - GET  /executions/{id}                 — the execution (carries task_id)
 *   - GET  /eval-datasets                   — dataset list for the picker
 *   - POST /eval-datasets                   — create dataset inline
 *   - POST /tasks/{taskId}/promote-to-dataset — promote (created true/false)
 *
 * Drives:
 *   - non-admin does NOT see the promote button (RoleGuard),
 *   - admin opens the dialog, picks a dataset, submits → POST with dataset_id,
 *   - inline-create flow → POST /eval-datasets then promote into the new id,
 *   - idempotent re-promote surfaces the "already in dataset" result,
 *   - the allow-unapproved checkbox flows through to the request body.
 *
 * NOTE: written, not run (task_14_02 e2e is pending human verification).
 */

const EXECUTION_ID = "e1111111-0000-0000-0000-000000000001";
const TASK_ID = "a2222222-0000-0000-0000-000000000002";
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const DATASET_ID = "d3333333-0000-0000-0000-000000000003";
const NEW_DATASET_ID = "d4444444-0000-0000-0000-000000000004";
const BASE = "http://localhost:8001";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@platform.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Acme", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const TENANT_USER = {
  ...TENANT_ADMIN,
  user_id: "88888888-0000-0000-0000-000000000088",
  email: "user@platform.test",
  full_name: "Plain User",
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Acme", role: "tenant_user", is_active: true },
  ],
};

interface DatasetFixture {
  id: string;
  name: string;
  description: string | null;
  kind: string;
  target_agent_id: string | null;
  target_role: string | null;
  item_count: number;
  created_at: string;
  updated_at: string;
}

function makeDataset(over: Partial<DatasetFixture> = {}): DatasetFixture {
  return {
    id: DATASET_ID,
    name: "Login golden",
    description: null,
    kind: "golden",
    target_agent_id: null,
    target_role: null,
    item_count: 2,
    created_at: "2026-05-30T10:00:00Z",
    updated_at: "2026-05-30T10:00:00Z",
    ...over,
  };
}

interface Capture {
  promotes: Array<{ dataset_id: string; execution_id: string | null; allow_unapproved: boolean }>;
  datasetCreates: Array<{ name: string }>;
}

async function setup(
  page: Page,
  identity: typeof TENANT_ADMIN | typeof TENANT_USER = TENANT_ADMIN,
  options: { datasets?: DatasetFixture[]; promoteCreated?: boolean } = {},
): Promise<Capture> {
  const capture: Capture = { promotes: [], datasetCreates: [] };
  const datasets = options.datasets ?? [makeDataset()];
  const promoteCreated = options.promoteCreated ?? true;

  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route(`${BASE}/me`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(identity) }),
  );

  await page.route(`${BASE}/executions/${EXECUTION_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: EXECUTION_ID,
        task_id: TASK_ID,
        status: "done",
        abort_code: null,
        output: "the approved diff",
        steps_log: [],
        iterations: 1,
        total_tokens: 100,
        total_cost_usd: 0.01,
      }),
    }),
  );

  await page.route(`${BASE}/eval-datasets`, (route: Route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(datasets),
      });
    }
    if (method === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      capture.datasetCreates.push(body);
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(makeDataset({ id: NEW_DATASET_ID, name: body.name, item_count: 0 })),
      });
    }
    return route.fallback();
  });

  await page.route(`${BASE}/tasks/${TASK_ID}/promote-to-dataset`, (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    capture.promotes.push(body);
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        id: "item-1",
        dataset_id: body.dataset_id,
        created: promoteCreated,
        expected_output: "the approved diff",
        source_task_id: TASK_ID,
        source_execution_id: EXECUTION_ID,
        created_at: "2026-05-30T12:00:00Z",
      }),
    });
  });

  return capture;
}

// ---------------------------------------------------------------------------
// RBAC — a plain user does NOT see the promote button
// ---------------------------------------------------------------------------
test("a plain user does not see the promote button", async ({ page }) => {
  await setup(page, TENANT_USER);
  await page.goto(`/admin/executions/${EXECUTION_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("execution-status")).toBeVisible();
  await expect(page.getByTestId("promote-to-dataset-button")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Admin promotes into an existing dataset
// ---------------------------------------------------------------------------
test("admin promotes the task into an existing dataset", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN);
  await page.goto(`/admin/executions/${EXECUTION_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("promote-to-dataset-button").click();
  await expect(page.getByTestId("promote-dialog")).toBeVisible();
  await page.getByTestId("promote-dataset-select").selectOption(DATASET_ID);
  await page.getByTestId("promote-submit").click();

  await expect.poll(() => capture.promotes.length).toBe(1);
  expect(capture.promotes[0]).toMatchObject({
    dataset_id: DATASET_ID,
    execution_id: EXECUTION_ID,
    allow_unapproved: false,
  });
  await expect(page.getByTestId("promote-result")).toContainText("nuevo item golden");
});

// ---------------------------------------------------------------------------
// Inline-create a dataset, then promote into it
// ---------------------------------------------------------------------------
test("admin creates a dataset inline and promotes into it", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN, { datasets: [] });
  await page.goto(`/admin/executions/${EXECUTION_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("promote-to-dataset-button").click();
  await page.getByTestId("promote-dataset-select").selectOption("__new__");
  await page.getByTestId("promote-new-name").fill("Fresh golden");
  await page.getByTestId("promote-submit").click();

  await expect.poll(() => capture.datasetCreates.length).toBe(1);
  expect(capture.datasetCreates[0]).toMatchObject({ name: "Fresh golden" });
  await expect.poll(() => capture.promotes.length).toBe(1);
  expect(capture.promotes[0]).toMatchObject({ dataset_id: NEW_DATASET_ID });
});

// ---------------------------------------------------------------------------
// Idempotent re-promote surfaces the "already in dataset" result
// ---------------------------------------------------------------------------
test("re-promoting an already-promoted task shows the idempotent result", async ({ page }) => {
  await setup(page, TENANT_ADMIN, { promoteCreated: false });
  await page.goto(`/admin/executions/${EXECUTION_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("promote-to-dataset-button").click();
  await page.getByTestId("promote-dataset-select").selectOption(DATASET_ID);
  await page.getByTestId("promote-submit").click();

  await expect(page.getByTestId("promote-result")).toContainText("no se ha duplicado");
});

// ---------------------------------------------------------------------------
// The allow-unapproved checkbox flows through to the request
// ---------------------------------------------------------------------------
test("the allow-unapproved checkbox is sent in the promote request", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN);
  await page.goto(`/admin/executions/${EXECUTION_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("promote-to-dataset-button").click();
  await page.getByTestId("promote-dataset-select").selectOption(DATASET_ID);
  await page.getByTestId("promote-allow-unapproved").check();
  await page.getByTestId("promote-submit").click();

  await expect.poll(() => capture.promotes.length).toBe(1);
  expect(capture.promotes[0]).toMatchObject({ allow_unapproved: true });
});
