import { expect, test, type Page, type Route } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Tools del agente" section in /admin/agents/{id}
 * (Plan 06.15 task_06_15_03).
 *
 * The section (`<AgentToolsSection>`) reads the catalog (`GET /tools`)
 * and the agent's current assignments (`GET /agents/{id}/tools`), shows
 * two tabs — BÁSICAS (is_builtin) vs AVANZADAS (custom · MCP · executors)
 * — with a checkbox per tool and a security_level + implementation_type
 * badge, and saves the whole declarative set via `PUT /agents/{id}/tools`.
 *
 * Drives:
 *   - render of both tabs with their counts,
 *   - básicas tab lists only builtin tools with the right badges,
 *   - avanzadas tab lists custom + executor + MCP tools,
 *   - pre-checking from the current assignment,
 *   - toggle + Save sends the expected declarative body,
 *   - read-only for a global_builtin agent (no checkboxes editable, no
 *     Save button).
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_06_15_03 — it is
 * PENDING HUMAN VERIFICATION (needs a browser + admin-panel dev server).
 * Run with `npx playwright test e2e/agent-tools-assign.spec.ts`.
 */

const API = "http://localhost:8001";
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const AGENT_ID = "33333333-0000-0000-0000-000000000001";
const BUILTIN_AGENT_ID = "33333333-0000-0000-0000-0000000000bb";

const READ_FILE_ID = "44444444-0000-0000-0000-000000000001";
const WRITE_FILE_ID = "44444444-0000-0000-0000-000000000002";
const WEATHER_ID = "44444444-0000-0000-0000-000000000003";
const LINT_ID = "44444444-0000-0000-0000-000000000004";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    {
      tenant_id: TENANT_ID,
      tenant_name: "Tenant A",
      role: "tenant_admin",
      is_active: true,
    },
  ],
  active_tenant_id: TENANT_ID,
};

function agentBody(scope: string) {
  return {
    id: scope === "global_builtin" ? BUILTIN_AGENT_ID : AGENT_ID,
    tenant_id: TENANT_ID,
    name: "Backend Dev",
    description: "Builds backend features.",
    avatar_url: null,
    agent_type: "ai",
    role: "backend_dev",
    system_prompt: "You are a backend dev.",
    model_config: {},
    memory_scope: "private",
    review_capability: false,
    max_concurrent_tasks: 1,
    is_template: false,
    scope,
    project_id: null,
    forked_from_agent_id: null,
    forked_from_version: null,
    anchored_version: null,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    deleted_at: null,
  };
}

const CATALOG = [
  {
    id: READ_FILE_ID,
    tenant_id: TENANT_ID,
    name: "read_file",
    description: "Reads a file from the project repo.",
    category: "file",
    implementation_type: "builtin",
    security_level: "safe",
    is_builtin: true,
  },
  {
    id: WRITE_FILE_ID,
    tenant_id: TENANT_ID,
    name: "write_file",
    description: "Writes a file in the task worktree.",
    category: "file",
    implementation_type: "builtin",
    security_level: "sandboxed",
    is_builtin: true,
  },
  {
    id: WEATHER_ID,
    tenant_id: TENANT_ID,
    name: "weather_lookup",
    description: "Custom HTTP weather endpoint.",
    category: "data",
    implementation_type: "http_endpoint",
    security_level: "safe",
    is_builtin: false,
  },
  {
    id: LINT_ID,
    tenant_id: TENANT_ID,
    name: "lint_python",
    description: "Lints python in a sandboxed container.",
    category: "code",
    implementation_type: "docker_command",
    security_level: "privileged",
    is_builtin: false,
  },
];

function assignedRow(id: string) {
  const t = CATALOG.find((c) => c.id === id)!;
  return {
    tool_id: t.id,
    name: t.name,
    description: t.description,
    category: t.category,
    implementation_type: t.implementation_type,
    security_level: t.security_level,
    is_builtin: t.is_builtin,
    config_override: null,
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function setup(
  page: Page,
  opts: {
    scope?: string;
    assigned?: string[];
    onPut?: (body: unknown) => void;
  } = {},
): Promise<void> {
  const scope = opts.scope ?? "project_local";
  const id = scope === "global_builtin" ? BUILTIN_AGENT_ID : AGENT_ID;
  const assigned = opts.assigned ?? [];

  await seedSession(page);

  await page.route(`${API}/me`, (route) => json(route, TENANT_ADMIN));
  await page.route(`${API}/agents/${id}`, (route) => json(route, agentBody(scope)));
  await page.route(`${API}/tools**`, (route) => json(route, CATALOG));
  await page.route(`${API}/agents/${id}/knowledge-bases`, (route) => json(route, []));
  await page.route(`${API}/agents/${id}/tools`, async (route) => {
    if (route.request().method() === "PUT") {
      opts.onPut?.(route.request().postDataJSON());
      // Echo back the requested set as the new assignment.
      const sent = route.request().postDataJSON() as { tools: { tool_id: string }[] };
      await json(
        route,
        sent.tools.map((t) => assignedRow(t.tool_id)),
      );
      return;
    }
    await json(route, assigned.map(assignedRow));
  });
}

// ---------------------------------------------------------------------------
// Tabs render with counts + correct split
// ---------------------------------------------------------------------------
test("renders Básicas / Avanzadas tabs with the derived split", async ({ page }) => {
  await setup(page, { assigned: [READ_FILE_ID] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("agent-tools-section")).toBeVisible();
  await expect(page.getByTestId("agent-tools-tab-basic")).toContainText("Básicas (2)");
  await expect(page.getByTestId("agent-tools-tab-advanced")).toContainText("Avanzadas (2)");

  // Default tab = básicas: builtins present, advanced absent.
  await expect(page.getByTestId(`agent-tool-row-${READ_FILE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`agent-tool-row-${WRITE_FILE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`agent-tool-row-${WEATHER_ID}`)).toHaveCount(0);

  // The assigned builtin is pre-checked.
  await expect(page.getByTestId(`agent-tool-checkbox-${READ_FILE_ID}`)).toBeChecked();
  await expect(page.getByTestId(`agent-tool-checkbox-${WRITE_FILE_ID}`)).not.toBeChecked();
});

// ---------------------------------------------------------------------------
// Advanced tab lists custom + executor tools with their badges
// ---------------------------------------------------------------------------
test("Avanzadas tab lists custom + executor tools with badges", async ({ page }) => {
  await setup(page, { assigned: [] });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-tools-tab-advanced").click();

  const weather = page.getByTestId(`agent-tool-row-${WEATHER_ID}`);
  await expect(weather).toBeVisible();
  await expect(weather).toContainText("weather_lookup");
  await expect(weather).toContainText("http_endpoint");
  await expect(weather).toContainText("safe");

  const lint = page.getByTestId(`agent-tool-row-${LINT_ID}`);
  await expect(lint).toContainText("docker_command");
  await expect(lint).toContainText("privileged");
});

// ---------------------------------------------------------------------------
// Toggle + Save sends the declarative body
// ---------------------------------------------------------------------------
test("toggle a tool and Save sends the full declarative set", async ({ page }) => {
  let putBody: unknown = null;
  await setup(page, {
    assigned: [READ_FILE_ID],
    onPut: (body) => {
      putBody = body;
    },
  });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  // Save is disabled until the selection changes.
  await expect(page.getByTestId("agent-tools-save")).toBeDisabled();

  await page.getByTestId(`agent-tool-checkbox-${WRITE_FILE_ID}`).check();
  await expect(page.getByTestId("agent-tools-save")).toBeEnabled();
  await page.getByTestId("agent-tools-save").click();

  await expect.poll(() => putBody).not.toBeNull();
  const body = putBody as { tools: { tool_id: string }[] };
  const ids = body.tools.map((t) => t.tool_id).sort();
  expect(ids).toEqual([READ_FILE_ID, WRITE_FILE_ID].sort());
});

// ---------------------------------------------------------------------------
// global_builtin agent → read-only (no Save, checkboxes disabled)
// ---------------------------------------------------------------------------
test("global_builtin agent is read-only", async ({ page }) => {
  await setup(page, { scope: "global_builtin", assigned: [READ_FILE_ID] });
  await page.goto(`/admin/agents/${BUILTIN_AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("agent-tools-section")).toBeVisible();
  await expect(page.getByTestId("agent-tools-save")).toHaveCount(0);
  await expect(page.getByTestId(`agent-tool-checkbox-${READ_FILE_ID}`)).toBeDisabled();
});
