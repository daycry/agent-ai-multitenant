import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for /admin/projects/{id}/incoming-webhooks (Plan 13 task_13_11).
 *
 * INCOMING webhooks config UI: a project owner / tenant_admin creates / lists /
 * rotates / disables the per-project webhook configs an external tool
 * (GitHub/Jira/Sentry/...) POSTs verified events to. The page is wrapped in
 * <RoleGuard min="tenant_admin">; the signing secret is shown ONCE on
 * create/rotate and never echoed afterwards.
 *
 * Mocks:
 *   - GET    /me                                              — identity (admin vs user)
 *   - GET    /projects/{id}                                   — breadcrumb name
 *   - GET    /projects/{id}/incoming-webhooks                 — config list
 *   - POST   /projects/{id}/incoming-webhooks                 — create (returns signing_secret ONCE)
 *   - PUT    /projects/{id}/incoming-webhooks/{cid}           — edit (name/enabled/mappings)
 *   - POST   /projects/{id}/incoming-webhooks/{cid}/rotate-secret — rotate (new secret ONCE)
 *   - DELETE /projects/{id}/incoming-webhooks/{cid}           — soft-delete
 *   - GET    /projects/{id}/incoming-webhooks/{cid}/deliveries — recent deliveries
 *
 * Drives:
 *   - non-admin sees the forbidden message (RoleGuard),
 *   - empty state,
 *   - create: dialog → fill → submit → POST → secret banner shown ONCE (and the
 *     clear secret never leaks into the list afterwards),
 *   - rendering of a config card with the full incoming URL + enabled badge,
 *   - disable via edit dialog → PUT enabled=false,
 *   - rotate → POST rotate-secret → new secret banner,
 *   - delete → DELETE,
 *   - recent deliveries panel renders verified events.
 */

const PROJECT_ID = "11111111-0000-0000-0000-000000000001";
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const CONFIG_ID = "22222222-0000-0000-0000-000000000002";
const BASE = "http://localhost:8001";

const SYSTEM_ADMIN = {
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
  ...SYSTEM_ADMIN,
  user_id: "88888888-0000-0000-0000-000000000088",
  email: "user@platform.test",
  full_name: "Plain User",
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Acme", role: "tenant_user", is_active: true },
  ],
};

interface ConfigFixture {
  id: string;
  project_id: string;
  origin: string;
  name: string;
  enabled: boolean;
  action_mappings: unknown[];
  last_event_at: string | null;
  created_at: string;
  updated_at: string;
  incoming_path: string;
}

function makeConfig(over: Partial<ConfigFixture> = {}): ConfigFixture {
  return {
    id: CONFIG_ID,
    project_id: PROJECT_ID,
    origin: "github",
    name: "CI on acme/api",
    enabled: true,
    action_mappings: [{ event_type: "github.pull_request_review", action: "create_task" }],
    last_event_at: null,
    created_at: "2026-05-30T10:00:00Z",
    updated_at: "2026-05-30T10:00:00Z",
    incoming_path: `/webhooks/incoming/github/${CONFIG_ID}`,
    ...over,
  };
}

interface Capture {
  posts: Array<{ origin: string; name: string; enabled: boolean; action_mappings: unknown[] }>;
  puts: Array<Record<string, unknown>>;
  rotates: number;
  deletes: number;
}

async function setup(
  page: Page,
  identity: typeof SYSTEM_ADMIN | typeof TENANT_USER = SYSTEM_ADMIN,
  initial: ConfigFixture[] = [],
): Promise<Capture> {
  const capture: Capture = { posts: [], puts: [], rotates: 0, deletes: 0 };
  let configs = [...initial];

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

  await page.route(`${BASE}/projects/${PROJECT_ID}`, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: PROJECT_ID, name: "Acme API" }),
      });
    }
    return route.fallback();
  });

  // List + create on the collection URL.
  await page.route(`${BASE}/projects/${PROJECT_ID}/incoming-webhooks`, (route: Route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(configs),
      });
    }
    if (method === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      capture.posts.push(body);
      const created = makeConfig({
        id: "33333333-0000-0000-0000-000000000003",
        origin: body.origin,
        name: body.name,
        enabled: body.enabled,
        action_mappings: body.action_mappings ?? [],
        incoming_path: `/webhooks/incoming/${body.origin}/33333333-0000-0000-0000-000000000003`,
      });
      configs = [...configs, created];
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ ...created, signing_secret: "secret-shown-once-CREATE" }),
      });
    }
    return route.fallback();
  });

  // Per-config PUT + DELETE.
  await page.route(`${BASE}/projects/${PROJECT_ID}/incoming-webhooks/${CONFIG_ID}`, (route) => {
    const method = route.request().method();
    if (method === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      capture.puts.push(body);
      configs = configs.map((c) => (c.id === CONFIG_ID ? { ...c, ...body } : c));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(configs.find((c) => c.id === CONFIG_ID)),
      });
    }
    if (method === "DELETE") {
      capture.deletes += 1;
      configs = configs.filter((c) => c.id !== CONFIG_ID);
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });

  await page.route(
    `${BASE}/projects/${PROJECT_ID}/incoming-webhooks/${CONFIG_ID}/rotate-secret`,
    (route) => {
      capture.rotates += 1;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...makeConfig(), signing_secret: "secret-shown-once-ROTATE" }),
      });
    },
  );

  await page.route(
    `${BASE}/projects/${PROJECT_ID}/incoming-webhooks/${CONFIG_ID}/deliveries`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "del-1",
            origin: "github",
            delivery_id: "gh-123",
            event_type: "pull_request_review",
            verified: true,
            received_at: "2026-05-30T11:00:00Z",
          },
        ]),
      }),
  );

  return capture;
}

// ---------------------------------------------------------------------------
// RBAC — a non-admin sees the forbidden message
// ---------------------------------------------------------------------------
test("non-admin is shown the forbidden message", async ({ page }) => {
  await setup(page, TENANT_USER);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("incoming-webhooks-forbidden")).toBeVisible();
  await expect(page.getByTestId("incoming-webhooks-page")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------
test("admin sees the empty state when no configs exist", async ({ page }) => {
  await setup(page, SYSTEM_ADMIN, []);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("incoming-webhooks-page")).toBeVisible();
  await expect(page.getByTestId("incoming-webhooks-empty")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Render — a config card with full URL + enabled badge
// ---------------------------------------------------------------------------
test("config card shows the full incoming URL and the enabled badge", async ({ page }) => {
  await setup(page, SYSTEM_ADMIN, [makeConfig()]);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId(`webhook-card-${CONFIG_ID}`)).toBeVisible();
  await expect(page.getByTestId(`webhook-url-${CONFIG_ID}`)).toContainText(
    `${BASE}/webhooks/incoming/github/${CONFIG_ID}`,
  );
  await expect(page.getByTestId(`webhook-enabled-${CONFIG_ID}`)).toBeVisible();
});

// ---------------------------------------------------------------------------
// Create — secret shown ONCE, never leaks into the list afterwards
// ---------------------------------------------------------------------------
test("creating a config shows the secret once and never echoes it after", async ({ page }) => {
  const capture = await setup(page, SYSTEM_ADMIN, []);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("webhook-add-button").click();
  await expect(page.getByTestId("webhook-dialog")).toBeVisible();
  await page.getByTestId("webhook-form-origin").selectOption("jira");
  await page.getByTestId("webhook-form-name").fill("Jira prod");
  await page.getByTestId("webhook-form-submit").click();

  await expect.poll(() => capture.posts.length).toBe(1);
  expect(capture.posts[0]).toMatchObject({ origin: "jira", name: "Jira prod", enabled: true });

  // The secret banner shows the clear secret exactly once.
  await expect(page.getByTestId("webhook-secret-banner")).toBeVisible();
  await expect(page.getByTestId("webhook-secret-value")).toHaveValue("secret-shown-once-CREATE");

  // Dismiss the banner — the secret is gone from the DOM and the list never
  // carries it (the list response has no signing_secret field).
  await page.getByTestId("webhook-secret-dismiss").click();
  await expect(page.getByTestId("webhook-secret-banner")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("secret-shown-once-CREATE");
});

// ---------------------------------------------------------------------------
// Disable via edit dialog — PUT enabled=false
// ---------------------------------------------------------------------------
test("editing a config to disable it PUTs enabled=false", async ({ page }) => {
  const capture = await setup(page, SYSTEM_ADMIN, [makeConfig()]);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId(`webhook-edit-${CONFIG_ID}`).click();
  await expect(page.getByTestId("webhook-dialog")).toBeVisible();
  // Origin is immutable on edit.
  await expect(page.getByTestId("webhook-form-origin")).toBeDisabled();
  await page.getByTestId("webhook-form-enabled").uncheck();
  await page.getByTestId("webhook-form-submit").click();

  await expect.poll(() => capture.puts.length).toBe(1);
  expect(capture.puts[0]).toMatchObject({ enabled: false });
});

// ---------------------------------------------------------------------------
// Rotate — new secret banner
// ---------------------------------------------------------------------------
test("rotating a secret shows the new secret once", async ({ page }) => {
  const capture = await setup(page, SYSTEM_ADMIN, [makeConfig()]);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });

  // The rotate button triggers a window.confirm — auto-accept it.
  page.on("dialog", (dialog) => {
    dialog.accept().catch(() => {});
  });

  await page.getByTestId(`webhook-rotate-${CONFIG_ID}`).click();
  await expect.poll(() => capture.rotates).toBe(1);
  await expect(page.getByTestId("webhook-secret-banner")).toBeVisible();
  await expect(page.getByTestId("webhook-secret-value")).toHaveValue("secret-shown-once-ROTATE");
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------
test("deleting a config calls DELETE and removes the card", async ({ page }) => {
  const capture = await setup(page, SYSTEM_ADMIN, [makeConfig()]);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });

  page.on("dialog", (dialog) => {
    dialog.accept().catch(() => {});
  });

  await page.getByTestId(`webhook-delete-${CONFIG_ID}`).click();
  await expect.poll(() => capture.deletes).toBe(1);
  await expect(page.getByTestId(`webhook-card-${CONFIG_ID}`)).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Recent deliveries panel
// ---------------------------------------------------------------------------
test("recent deliveries panel lists verified events", async ({ page }) => {
  await setup(page, SYSTEM_ADMIN, [makeConfig()]);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId(`webhook-deliveries-toggle-${CONFIG_ID}`).click();
  await expect(page.getByTestId(`webhook-deliveries-list-${CONFIG_ID}`)).toBeVisible();
  await expect(page.getByTestId("webhook-delivery-del-1")).toContainText("pull_request_review");
});

// ---------------------------------------------------------------------------
// Add mapping rule with a comment action exposes the target-task input
// ---------------------------------------------------------------------------
test("a comment mapping rule exposes the target-task input", async ({ page }) => {
  await setup(page, SYSTEM_ADMIN, []);
  await page.goto(`/admin/projects/${PROJECT_ID}/incoming-webhooks`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("webhook-add-button").click();
  await page.getByTestId("webhook-form-add-rule").click();
  // create_task is the default — no target input.
  await expect(page.getByTestId("webhook-form-rule-target-0")).toHaveCount(0);
  await page.getByTestId("webhook-form-rule-action-0").selectOption("comment");
  await expect(page.getByTestId("webhook-form-rule-target-0")).toBeVisible();
});
