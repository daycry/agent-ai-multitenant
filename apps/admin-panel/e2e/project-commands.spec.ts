import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for /admin/projects/{id}/commands (Plan 06.16 task_06_16_04).
 *
 * "Comandos & runtime" UI: a tenant_admin authorises which stack binaries
 * `shell_exec` may run (the deny-by-default `allowed_commands` allowlist,
 * shown as removable chips + stack presets) and picks the project's
 * `default_runtime_template`. A non-admin sees the chips + runtime in
 * read-only mode (no add / remove / preset / save controls). Persists via
 * PUT /projects/{id}.
 *
 * Mocks:
 *   - GET  /me                  — identity (tenant_admin vs tenant_user)
 *   - GET  /projects/{id}       — project (breadcrumb name + the two fields)
 *   - PUT  /projects/{id}       — capture the update body
 *
 * Drives:
 *   - read-only mode for a non-admin (chips render, no edit affordances),
 *   - the PHP preset fills the chips,
 *   - add a command via the input + Enter,
 *   - remove a chip,
 *   - pick a runtime template + save → PUT carries allowed_commands + runtime,
 *   - deny-by-default hint + empty state are visible.
 *
 * NOTE: written, NOT run (per the task — typecheck/lint/build is the gate).
 */

const PROJECT_ID = "11111111-0000-0000-0000-000000000001";
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
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

interface ProjectFixture {
  id: string;
  name: string;
  allowed_commands: string[];
  default_runtime_template: string | null;
}

function makeProject(over: Partial<ProjectFixture> = {}): ProjectFixture {
  return {
    id: PROJECT_ID,
    name: "Acme API",
    allowed_commands: [],
    default_runtime_template: null,
    ...over,
  };
}

interface Capture {
  puts: Array<Record<string, unknown>>;
}

async function setup(
  page: Page,
  identity: typeof TENANT_ADMIN | typeof TENANT_USER = TENANT_ADMIN,
  project: ProjectFixture = makeProject(),
): Promise<Capture> {
  const capture: Capture = { puts: [] };
  let current = { ...project };

  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route(`${BASE}/me`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(identity),
    }),
  );

  await page.route(`${BASE}/projects/${PROJECT_ID}`, (route: Route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(current),
      });
    }
    if (method === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      capture.puts.push(body);
      current = { ...current, ...body };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(current),
      });
    }
    return route.fallback();
  });

  return capture;
}

// ---------------------------------------------------------------------------
// Read-only mode for a non-admin
// ---------------------------------------------------------------------------
test("non-admin sees chips + runtime read-only (no edit controls)", async ({ page }) => {
  await setup(
    page,
    TENANT_USER,
    makeProject({ allowed_commands: ["php", "composer"], default_runtime_template: "php-phpunit" }),
  );
  await page.goto(`/admin/projects/${PROJECT_ID}/commands`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("project-commands-page")).toBeVisible();
  // Chips render…
  await expect(page.getByTestId("command-chip-php")).toBeVisible();
  await expect(page.getByTestId("command-chip-composer")).toBeVisible();
  // …but no edit affordances.
  await expect(page.getByTestId("commands-presets")).toHaveCount(0);
  await expect(page.getByTestId("commands-add-input")).toHaveCount(0);
  await expect(page.getByTestId("command-chip-remove-php")).toHaveCount(0);
  await expect(page.getByTestId("commands-save-button")).toHaveCount(0);
  // Runtime is shown read-only.
  await expect(page.getByTestId("commands-runtime-readonly")).toContainText("php-phpunit");
  await expect(page.getByTestId("commands-runtime-select")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Empty state + deny-by-default hint
// ---------------------------------------------------------------------------
test("empty allowlist shows the deny-by-default hint and empty state", async ({ page }) => {
  await setup(page, TENANT_ADMIN, makeProject({ allowed_commands: [] }));
  await page.goto(`/admin/projects/${PROJECT_ID}/commands`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("commands-deny-by-default-hint")).toContainText("Deny-by-default");
  await expect(page.getByTestId("commands-empty")).toBeVisible();
  await expect(page.getByTestId("commands-privileged-badge")).toBeVisible();
});

// ---------------------------------------------------------------------------
// PHP preset fills the chips
// ---------------------------------------------------------------------------
test("the PHP preset fills the chips", async ({ page }) => {
  await setup(page, TENANT_ADMIN, makeProject({ allowed_commands: [] }));
  await page.goto(`/admin/projects/${PROJECT_ID}/commands`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("commands-preset-php").click();
  await expect(page.getByTestId("command-chip-php")).toBeVisible();
  await expect(page.getByTestId("command-chip-composer")).toBeVisible();
  await expect(page.getByTestId("command-chip-vendor/bin/phpunit")).toBeVisible();
  await expect(page.getByTestId("command-chip-pest")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Add a command via the input + Enter
// ---------------------------------------------------------------------------
test("adding a command via the input creates a chip", async ({ page }) => {
  await setup(page, TENANT_ADMIN, makeProject({ allowed_commands: [] }));
  await page.goto(`/admin/projects/${PROJECT_ID}/commands`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("commands-add-input").fill("dotnet");
  await page.getByTestId("commands-add-button").click();
  await expect(page.getByTestId("command-chip-dotnet")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Remove a chip
// ---------------------------------------------------------------------------
test("removing a chip drops the command", async ({ page }) => {
  await setup(page, TENANT_ADMIN, makeProject({ allowed_commands: ["php", "composer"] }));
  await page.goto(`/admin/projects/${PROJECT_ID}/commands`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("command-chip-remove-php").click();
  await expect(page.getByTestId("command-chip-php")).toHaveCount(0);
  await expect(page.getByTestId("command-chip-composer")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Save → PUT carries allowed_commands + default_runtime_template
// ---------------------------------------------------------------------------
test("saving PUTs the allowlist and the chosen runtime", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN, makeProject({ allowed_commands: [] }));
  await page.goto(`/admin/projects/${PROJECT_ID}/commands`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("commands-preset-php").click();
  await page.getByTestId("commands-runtime-select").selectOption("php-phpunit");
  await page.getByTestId("commands-save-button").click();

  await expect.poll(() => capture.puts.length).toBe(1);
  expect(capture.puts[0]).toMatchObject({
    allowed_commands: ["php", "composer", "vendor/bin/phpunit", "pest"],
    default_runtime_template: "php-phpunit",
  });
  await expect(page.getByTestId("commands-saved")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Clearing the runtime sends null
// ---------------------------------------------------------------------------
test("clearing the runtime back to default sends null", async ({ page }) => {
  const capture = await setup(
    page,
    TENANT_ADMIN,
    makeProject({ allowed_commands: ["php"], default_runtime_template: "php-phpunit" }),
  );
  await page.goto(`/admin/projects/${PROJECT_ID}/commands`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("commands-runtime-select").selectOption("");
  await page.getByTestId("commands-save-button").click();

  await expect.poll(() => capture.puts.length).toBe(1);
  expect(capture.puts[0]).toMatchObject({
    allowed_commands: ["php"],
    default_runtime_template: null,
  });
});
