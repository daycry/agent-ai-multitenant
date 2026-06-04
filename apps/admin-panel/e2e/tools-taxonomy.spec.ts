import { expect, test, type Page } from "@playwright/test";

/**
 * E2E (DELIVERABLE — written for CI/human, NOT executed in this environment;
 * it needs a live Next dev server + api-server) for Plan 06.18 task_06_18_10:
 * the shared tools taxonomy + the corrected read-only diagnostic.
 *
 * Drives `/admin/projects/{id}/agent-tools-diagnostic`, which now consumes:
 *   1. GET /projects/{id}/agent-tools-diagnostic  — agents + MCP servers,
 *   2. GET /agents/{id}/effective-tools           — the honest effective set
 *      per agent (task_06_18_07), with executability + warnings.
 *
 * Asserts the regressions this task fixed:
 *   - the read-only verification banner is present,
 *   - taxonomy labels are the SHARED human ones (e.g. "Aislada" for
 *     `sandboxed`, "Contenedor" for `docker_command`), never the raw enum,
 *   - the invented `sensitive` enum value never appears, even if a tool
 *     carried it (it falls back to a humanised label, not the slug),
 *   - effective-tools warnings surface,
 *   - a non-executable assignment is flagged "No disponible aún".
 *
 * The CONTRACT that the same tool renders identical label/variant in BOTH
 * the assignment screen and this diagnostic is covered by the vitest unit test
 * (`lib/tools/tools-taxonomy.test.ts`) — both import the single source module.
 */

const PROJECT_ID = "22222222-0000-0000-0000-000000000001";
const AGENT_ID = "33333333-0000-0000-0000-000000000001";

interface ToolFixture {
  id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  security_level: string;
  timeout_seconds: number;
  executable_in_runtime: boolean;
}

interface AgentFixture {
  id: string;
  name: string;
  role: string;
  scope: string;
  tools: ToolFixture[];
}

interface McpFixture {
  name: string;
  transport: string;
  has_auth: boolean;
}

interface EffectiveEntryFixture {
  tool_id: string;
  name: string;
  canonical_names: string[];
  category: string;
  implementation_type: string;
  security_level: string;
  is_builtin: boolean;
  executable_in_runtime: boolean;
}

interface EffectiveFixture {
  agent_id: string;
  mode: string | null;
  assigned: EffectiveEntryFixture[];
  effective: string[];
  unrestricted: boolean;
  shell_exec_effective: boolean;
  warnings: string[];
}

async function setup(
  page: Page,
  opts: {
    agents: AgentFixture[];
    mcp_servers: McpFixture[];
    effective: Record<string, EffectiveFixture>;
  },
): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`http://localhost:8001/projects/${PROJECT_ID}/agent-tools-diagnostic`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        project_id: PROJECT_ID,
        agents: opts.agents,
        mcp_servers: opts.mcp_servers,
      }),
    }),
  );
  // Per-agent effective-tools — match any agent id, return its fixture.
  await page.route(/\/agents\/[^/]+\/effective-tools/, (route) => {
    const url = route.request().url();
    const match = url.match(/\/agents\/([^/]+)\/effective-tools/);
    const agentId = match?.[1] ?? "";
    const body = opts.effective[agentId];
    route.fulfill({
      status: body ? 200 : 404,
      contentType: "application/json",
      body: JSON.stringify(body ?? { detail: "agent not found" }),
    });
  });
}

test("read-only banner is shown on the diagnostic", async ({ page }) => {
  await setup(page, { agents: [], mcp_servers: [], effective: {} });
  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("agent-tools-diagnostic-readonly-banner")).toBeVisible();
  await expect(page.getByTestId("agent-tools-diagnostic-readonly-banner")).toContainText(
    "Solo lectura",
  );
});

test("taxonomy labels are the shared human ones, never the raw enum", async ({ page }) => {
  const agent: AgentFixture = {
    id: AGENT_ID,
    name: "Backend Dev",
    role: "backend-dev",
    scope: "project_local",
    tools: [
      {
        id: "44444444-0000-0000-0000-000000000001",
        name: "lint_python",
        description: "Lint in a sandboxed container.",
        category: "runtime",
        implementation_type: "docker_command",
        security_level: "sandboxed",
        timeout_seconds: 30,
        executable_in_runtime: true,
      },
    ],
  };
  await setup(page, {
    agents: [agent],
    mcp_servers: [],
    effective: {
      [AGENT_ID]: {
        agent_id: AGENT_ID,
        mode: null,
        assigned: agent.tools.map((t) => ({
          tool_id: t.id,
          name: t.name,
          canonical_names: [t.name],
          category: t.category,
          implementation_type: t.implementation_type,
          security_level: t.security_level,
          is_builtin: true,
          executable_in_runtime: t.executable_in_runtime,
        })),
        effective: ["lint_python"],
        unrestricted: false,
        shell_exec_effective: false,
        warnings: [],
      },
    },
  });
  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });

  const card = page.getByTestId(`diagnostic-agent-card-${AGENT_ID}`);
  await expect(card).toBeVisible();
  // Human labels from the shared taxonomy module.
  await expect(card).toContainText("Aislada"); // sandboxed
  await expect(card).toContainText("Contenedor"); // docker_command
  // The raw enum is NEVER rendered.
  await expect(card).not.toContainText("sandboxed");
  await expect(card).not.toContainText("docker_command");
});

test("invented 'sensitive' enum never renders verbatim", async ({ page }) => {
  const agent: AgentFixture = {
    id: AGENT_ID,
    name: "Reviewer",
    role: "reviewer",
    scope: "project_local",
    tools: [
      {
        id: "44444444-0000-0000-0000-000000000009",
        name: "legacy_tool",
        description: null,
        category: "custom",
        implementation_type: "python_function",
        // A stale row that still carries the invented value: the UI must
        // humanise it, never show the slug.
        security_level: "sensitive",
        timeout_seconds: 10,
        executable_in_runtime: true,
      },
    ],
  };
  await setup(page, {
    agents: [agent],
    mcp_servers: [],
    effective: {
      [AGENT_ID]: {
        agent_id: AGENT_ID,
        mode: null,
        assigned: agent.tools.map((t) => ({
          tool_id: t.id,
          name: t.name,
          canonical_names: [t.name],
          category: t.category,
          implementation_type: t.implementation_type,
          security_level: t.security_level,
          is_builtin: false,
          executable_in_runtime: true,
        })),
        effective: ["legacy_tool"],
        unrestricted: false,
        shell_exec_effective: false,
        warnings: [],
      },
    },
  });
  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });
  const card = page.getByTestId(`diagnostic-agent-card-${AGENT_ID}`);
  await expect(card).toBeVisible();
  await expect(card).not.toContainText("sensitive");
});

test("effective-tools warnings and non-executable flag surface", async ({ page }) => {
  const agent: AgentFixture = {
    id: AGENT_ID,
    name: "Backend Dev",
    role: "backend-dev",
    scope: "project_local",
    tools: [
      {
        id: "44444444-0000-0000-0000-000000000002",
        name: "weather_lookup",
        description: null,
        category: "network",
        implementation_type: "http_endpoint",
        security_level: "safe",
        timeout_seconds: 10,
        executable_in_runtime: false,
      },
    ],
  };
  await setup(page, {
    agents: [agent],
    mcp_servers: [],
    effective: {
      [AGENT_ID]: {
        agent_id: AGENT_ID,
        mode: null,
        assigned: agent.tools.map((t) => ({
          tool_id: t.id,
          name: t.name,
          canonical_names: [t.name],
          category: t.category,
          implementation_type: t.implementation_type,
          security_level: t.security_level,
          is_builtin: false,
          executable_in_runtime: false,
        })),
        effective: [],
        unrestricted: false,
        shell_exec_effective: false,
        warnings: ["weather_lookup está asignada pero el runtime no la puede ejecutar."],
      },
    },
  });
  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });
  await expect(
    page.getByTestId(`diagnostic-tool-not-wired-44444444-0000-0000-0000-000000000002`),
  ).toBeVisible();
  await expect(page.getByTestId(`diagnostic-agent-warnings-${AGENT_ID}`)).toContainText(
    "no la puede ejecutar",
  );
});
