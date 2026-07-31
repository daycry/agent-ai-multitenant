import { expect, test, type Page, type Route } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the navigable tool catalog at /admin/tools (Plan 06.18
 * task_06_18_11).
 *
 * Verifies the operator-facing fixes called out by the plan:
 *   - the catalog lists every tool with the THREE ADR-0049 facet badges
 *     (Función / Seguridad / Origen), using the SAME shared taxonomy
 *     resolvers + card-row pattern as the assignment screen,
 *   - faceted browse narrows by each facet and free-text search filters,
 *   - built-in tools are read-only (a "Solo lectura" badge, no edit/delete),
 *     custom tools expose edit + delete,
 *   - creating a custom tool whose name collides with a built-in is rejected
 *     (the backend's 409 surfaces as a friendly duplicate message — no two
 *     identical rows),
 *   - a tool with no runtime engine shows "No disponible aún",
 *   - the sidebar exposes a "Catálogo" item, and the agent-tools empty state
 *     links here with a real clickable <Link> (no more dead plain-text /tools).
 *
 * NOTE: this spec is WRITTEN as a DELIVERABLE but NOT executed as part of
 * task_06_18_11 — it needs a browser + admin-panel dev server + a live
 * backend, none of which exist in the implementation environment. The green
 * gate for this task is typecheck + lint + build (+ vitest for the taxonomy
 * module). Run with `npx playwright test e2e/tools-catalog.spec.ts`.
 */

const API = "http://localhost:8001";
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const AGENT_ID = "33333333-0000-0000-0000-000000000001";

const READ_FILE_ID = "44444444-0000-0000-0000-000000000001";
const RUN_TESTS_ID = "44444444-0000-0000-0000-000000000005";
const GIT_COMMIT_ID = "44444444-0000-0000-0000-000000000007";
const CUSTOM_DEPLOY_ID = "44444444-0000-0000-0000-0000000000c1";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

// Mirror api_server.schemas.catalog.ToolResponse (incl. is_runtime_wired).
const CATALOG = [
  {
    id: READ_FILE_ID,
    tenant_id: TENANT_ID,
    name: "read_file",
    description: "Reads a file from the project repo.",
    category: "file",
    implementation_type: "builtin",
    implementation_ref: null,
    security_level: "safe",
    is_builtin: true,
    is_runtime_wired: true,
  },
  {
    id: RUN_TESTS_ID,
    tenant_id: TENANT_ID,
    name: "run_tests",
    description: "Runs the test suite in an ephemeral container.",
    category: "runtime",
    implementation_type: "docker_command",
    implementation_ref: null,
    security_level: "privileged",
    is_builtin: true,
    is_runtime_wired: true,
  },
  {
    // A built-in with NO runtime engine → must render "No disponible aún".
    id: GIT_COMMIT_ID,
    tenant_id: TENANT_ID,
    name: "git_commit",
    description: "Commits staged changes.",
    category: "git",
    implementation_type: "builtin",
    implementation_ref: null,
    security_level: "sandboxed",
    is_builtin: true,
    is_runtime_wired: false,
  },
  {
    id: CUSTOM_DEPLOY_ID,
    tenant_id: TENANT_ID,
    name: "deploy_preview",
    description: "Calls the preview-deploy webhook.",
    category: "custom",
    implementation_type: "http_endpoint",
    implementation_ref: "https://hooks.example.test/deploy",
    security_level: "safe",
    is_builtin: false,
    is_runtime_wired: true,
  },
];

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function setupCatalog(page: Page): Promise<void> {
  await seedSession(page);
  await page.route(`${API}/me`, (route) => json(route, TENANT_ADMIN));
  await page.route(`${API}/tools**`, (route) => {
    // List call (GET) returns the catalog. POST/PUT/DELETE handled per-test.
    if (route.request().method() === "GET") return json(route, CATALOG);
    return route.fallback();
  });
}

// ---------------------------------------------------------------------------
// Catalog lists every tool with the three facet badges
// ---------------------------------------------------------------------------
test("lists the catalog with the three facets and the shared card-row", async ({ page }) => {
  await setupCatalog(page);
  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("tools-catalog-page")).toBeVisible();

  // Built-in and custom groups both render.
  await expect(page.getByTestId("tools-group-builtin-list")).toBeVisible();
  await expect(page.getByTestId("tools-group-custom-list")).toBeVisible();

  // A built-in row carries the three facet badges (Función/Seguridad/Origen).
  await expect(page.getByTestId(`tool-category-badge-${RUN_TESTS_ID}`)).toBeVisible();
  await expect(page.getByTestId(`tool-security-badge-${RUN_TESTS_ID}`)).toBeVisible();
  await expect(page.getByTestId(`tool-impl-badge-${RUN_TESTS_ID}`)).toBeVisible();

  // Labels are human (shared taxonomy), never the raw enum.
  await expect(page.getByTestId(`tool-impl-badge-${RUN_TESTS_ID}`)).toContainText("Contenedor");
  await expect(page.getByTestId(`tool-security-badge-${RUN_TESTS_ID}`)).toContainText(
    "Privilegiada",
  );
  await expect(page.getByTestId(`tool-impl-badge-${RUN_TESTS_ID}`)).not.toContainText(
    "docker_command",
  );
});

// ---------------------------------------------------------------------------
// Built-in read-only, custom editable
// ---------------------------------------------------------------------------
test("built-in tools are read-only and custom tools are editable", async ({ page }) => {
  await setupCatalog(page);
  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });

  // Built-in: read-only badge, no edit/delete.
  await expect(page.getByTestId(`tool-readonly-badge-${READ_FILE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`tool-edit-${READ_FILE_ID}`)).toHaveCount(0);
  await expect(page.getByTestId(`tool-delete-${READ_FILE_ID}`)).toHaveCount(0);

  // Custom: edit + delete affordances present.
  await expect(page.getByTestId(`tool-edit-${CUSTOM_DEPLOY_ID}`)).toBeVisible();
  await expect(page.getByTestId(`tool-delete-${CUSTOM_DEPLOY_ID}`)).toBeVisible();
});

// ---------------------------------------------------------------------------
// A tool with no runtime engine shows "No disponible aún"
// ---------------------------------------------------------------------------
test("a tool without a runtime engine is flagged 'No disponible aún'", async ({ page }) => {
  await setupCatalog(page);
  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId(`tool-unwired-badge-${GIT_COMMIT_ID}`)).toBeVisible();
  await expect(page.getByTestId(`tool-unwired-badge-${GIT_COMMIT_ID}`)).toContainText(
    "No disponible aún",
  );
  // A wired tool does NOT carry the flag.
  await expect(page.getByTestId(`tool-unwired-badge-${READ_FILE_ID}`)).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Faceted browse + search narrow the list
// ---------------------------------------------------------------------------
test("filtering by the Function facet narrows the catalog", async ({ page }) => {
  await setupCatalog(page);
  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });

  // Narrow to Git → only git_commit remains.
  await page.getByTestId("tools-facet-category").selectOption("git");
  await expect(page.getByTestId(`tool-row-${GIT_COMMIT_ID}`)).toBeVisible();
  await expect(page.getByTestId(`tool-row-${READ_FILE_ID}`)).toHaveCount(0);
  await expect(page.getByTestId(`tool-row-${CUSTOM_DEPLOY_ID}`)).toHaveCount(0);
});

test("free-text search filters by name", async ({ page }) => {
  await setupCatalog(page);
  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });

  await page.getByTestId("tools-search").fill("deploy");
  await expect(page.getByTestId(`tool-row-${CUSTOM_DEPLOY_ID}`)).toBeVisible();
  await expect(page.getByTestId(`tool-row-${READ_FILE_ID}`)).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Creating a custom tool that collides with a built-in is rejected (409)
// ---------------------------------------------------------------------------
test("rejects a custom tool whose name collides with a built-in (no duplicate row)", async ({
  page,
}) => {
  await setupCatalog(page);
  await page.route(`${API}/tools`, async (route) => {
    if (route.request().method() === "POST") {
      await json(route, { detail: "name 'read_file' collides with a platform built-in tool" }, 409);
      return;
    }
    await route.fallback();
  });

  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });

  await page.getByTestId("tools-create-button").click();
  await expect(page.getByTestId("tool-form-dialog")).toBeVisible();
  await page.getByTestId("tool-form-name").fill("read_file");
  await page.getByTestId("tool-form-submit").click();

  // Friendly duplicate message, dialog stays open, no second read_file row.
  await expect(page.getByTestId("tool-form-error")).toBeVisible();
  await expect(page.getByTestId("tool-form-error")).toContainText("Ya existe");
});

// ---------------------------------------------------------------------------
// Sidebar exposes a "Catálogo" item
// ---------------------------------------------------------------------------
test("the sidebar exposes a Catálogo item linking to /admin/tools", async ({ page }) => {
  await setupCatalog(page);
  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });

  const item = page.getByTestId("nav-tools");
  await expect(item).toBeVisible();
  await expect(item).toHaveAttribute("href", "/admin/tools");
});

// ---------------------------------------------------------------------------
// The agent-tools empty state links to the catalog with a real <Link>
// ---------------------------------------------------------------------------
test("the agent-tools empty state links to the catalog (clickable, not plain text)", async ({
  page,
}) => {
  await seedSession(page);
  await page.route(`${API}/me`, (route) => json(route, TENANT_ADMIN));
  await page.route(`${API}/agents/${AGENT_ID}`, (route) =>
    json(route, {
      id: AGENT_ID,
      tenant_id: TENANT_ID,
      name: "Backend Dev",
      description: null,
      avatar_url: null,
      agent_type: "ai",
      role: "backend_dev",
      system_prompt: "x",
      model_config: {},
      memory_scope: "private",
      review_capability: false,
      max_concurrent_tasks: 1,
      is_template: false,
      scope: "project_local",
      project_id: null,
      forked_from_agent_id: null,
      forked_from_version: null,
      anchored_version: null,
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
      deleted_at: null,
    }),
  );
  await page.route(`${API}/agents/${AGENT_ID}/knowledge-bases`, (route) => json(route, []));
  await page.route(`${API}/agents/${AGENT_ID}/tools`, (route) => json(route, []));
  // Catalog with ONLY built-in tools → the "advanced" (custom) tab is empty,
  // so the empty state with the catalog link renders.
  await page.route(`${API}/tools**`, (route) =>
    json(
      route,
      CATALOG.filter((t) => t.is_builtin),
    ),
  );

  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-tools-tab-advanced").click();
  const link = page.getByTestId("agent-tools-catalog-link");
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", "/admin/tools");
});

// ---------------------------------------------------------------------------
// The same tool reads identically in the catalog and the assignment screen
// ---------------------------------------------------------------------------
test("the same tool shows the same label in catalog and assignment", async ({ page }) => {
  await setupCatalog(page);
  await page.goto("/admin/tools", { waitUntil: "domcontentloaded" });
  const catalogImpl = await page.getByTestId(`tool-impl-badge-${RUN_TESTS_ID}`).innerText();
  expect(catalogImpl).toContain("Contenedor");
});
