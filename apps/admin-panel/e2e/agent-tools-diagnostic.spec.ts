import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/projects/{id}/agent-tools-diagnostic (Plan 05 task_05_15).
 *
 * The page is read-only — `page.route()` mocks
 * `GET /projects/{id}/agent-tools-diagnostic`.
 *
 * Drives:
 *   - empty state when no project-scoped agents are declared,
 *   - empty MCP servers card when project.mcp_servers is empty,
 *   - render of one agent with its wired Tool rows + the impl-type
 *     badges (builtin / mcp_tool / http_endpoint / python_function /
 *     docker_command),
 *   - render of the project's MCP servers card with auth badge.
 *
 * Reparado el 2026-08-19 (subset mockeado de CI). El spec afirmaba que la
 * tarjeta contenía los ENUM crudos (`builtin`, `http_endpoint`,
 * `docker_command`, `privileged`), y el ADR 0049 decidió justo lo contrario:
 * la taxonomía de tools se pinta con etiquetas humanas bilingües
 * (`lib/tools/taxonomy.ts`) y **el enum crudo no se renderiza nunca**. O sea que
 * el test no sólo estaba desfasado: vigilaba lo contrario de lo acordado. Ahora
 * comprueba la etiqueta Y que el slug NO aparece, que es el contrato de verdad.
 *
 * De paso, el fixture usaba `security_level: "sensitive"`, un valor que el enum
 * del backend nunca tuvo (lo dice el comentario de `taxonomy.ts`): el mock
 * describía un backend imposible. Los tres niveles reales son
 * safe / sandboxed / privileged, y ahora salen los tres.
 */

const PROJECT_ID = "22222222-0000-0000-0000-000000000001";

interface ToolFixture {
  id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  security_level: string;
  timeout_seconds: number;
  /** Lo que decide el badge "No disponible aún" (honestidad de estado). */
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

async function setup(
  page: Page,
  body: {
    agents: AgentFixture[];
    mcp_servers: McpFixture[];
  },
): Promise<void> {
  await seedSession(page);
  await page.route(`http://localhost:8001/projects/${PROJECT_ID}/agent-tools-diagnostic`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ project_id: PROJECT_ID, ...body }),
    }),
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------
test("empty project shows both empty messages", async ({ page }) => {
  await setup(page, { agents: [], mcp_servers: [] });
  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("agent-tools-diagnostic-page")).toBeVisible();
  await expect(page.getByTestId("diagnostic-mcp-empty")).toBeVisible();
  await expect(page.getByTestId("diagnostic-agents-empty")).toBeVisible();
});

// ---------------------------------------------------------------------------
// MCP servers card with one Vault-backed entry
// ---------------------------------------------------------------------------
test("MCP servers card renders entries + auth badge", async ({ page }) => {
  await setup(page, {
    agents: [],
    mcp_servers: [
      { name: "docling", transport: "stdio", has_auth: false },
      { name: "github", transport: "streamable_http", has_auth: true },
    ],
  });
  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("diagnostic-mcp-list")).toBeVisible();
  await expect(page.getByTestId("diagnostic-mcp-docling")).toBeVisible();
  await expect(page.getByTestId("diagnostic-mcp-github")).toBeVisible();

  // docling has no auth → no "vault" badge inside its row;
  // github does → has it.
  await expect(page.getByTestId("diagnostic-mcp-docling")).not.toContainText("vault");
  await expect(page.getByTestId("diagnostic-mcp-github")).toContainText("vault");
});

// ---------------------------------------------------------------------------
// Agent with mixed tools — every impl_type badge surfaces
// ---------------------------------------------------------------------------
test("agent card lists wired tools with impl-type + security badges", async ({ page }) => {
  const AGENT_ID = "33333333-0000-0000-0000-000000000001";
  await setup(page, {
    agents: [
      {
        id: AGENT_ID,
        name: "Backend Dev",
        role: "backend-dev",
        scope: "project_local",
        tools: [
          {
            id: "44444444-0000-0000-0000-000000000001",
            name: "shell_exec",
            description: "Run a shell command in the workspace.",
            category: "system",
            implementation_type: "builtin",
            security_level: "privileged",
            timeout_seconds: 60,
            executable_in_runtime: true,
          },
          {
            id: "44444444-0000-0000-0000-000000000002",
            name: "weather_lookup",
            description: null,
            category: "data",
            implementation_type: "http_endpoint",
            security_level: "safe",
            timeout_seconds: 10,
            executable_in_runtime: true,
          },
          {
            id: "44444444-0000-0000-0000-000000000003",
            name: "lint_python",
            description: "Pyflakes-style lint in a sandboxed container.",
            category: "code",
            implementation_type: "docker_command",
            security_level: "sandboxed",
            timeout_seconds: 30,
            executable_in_runtime: true,
          },
        ],
      },
    ],
    mcp_servers: [],
  });

  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId(`diagnostic-agent-card-${AGENT_ID}`)).toBeVisible();
  await expect(page.getByTestId(`diagnostic-agent-tool-count-${AGENT_ID}`)).toHaveText("3 tools");

  // Cada fila con su dato. El NOMBRE de la tool sí es literal (es el
  // identificador que el agente invoca); lo que se traduce es la taxonomía.
  const card = page.getByTestId(`diagnostic-agent-card-${AGENT_ID}`);
  await expect(card).toContainText("shell_exec");
  await expect(card).toContainText("Nativa");
  await expect(card).toContainText("Privilegiada");

  await expect(card).toContainText("weather_lookup");
  await expect(card).toContainText("HTTP");
  await expect(card).toContainText("Segura");

  await expect(card).toContainText("lint_python");
  await expect(card).toContainText("Contenedor");
  await expect(card).toContainText("Aislada");

  // ADR 0049, la mitad que faltaba: el enum crudo NO se enseña. Sin esto, la
  // pantalla podría volver a pintar `docker_command` al lado de "Contenedor" y
  // el test seguiría en verde.
  //
  // Se busca el slug como texto EXACTO de un elemento (que es como saldría un
  // badge), no como subcadena de la tarjeta: la descripción de una tool puede
  // mencionar legítimamente la palabra ("...in a sandboxed container").
  for (const raw of [
    "builtin",
    "http_endpoint",
    "docker_command",
    "safe",
    "sandboxed",
    "privileged",
  ]) {
    await expect(card.getByText(raw, { exact: true })).toHaveCount(0);
  }

  // Las tres están cableadas: ningún badge de "No disponible aún".
  await expect(card.getByText("No disponible aún")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Agent without wired tools shows the empty hint
// ---------------------------------------------------------------------------
test("agent without wired tools shows the empty hint", async ({ page }) => {
  const AGENT_ID = "33333333-0000-0000-0000-000000000002";
  await setup(page, {
    agents: [
      {
        id: AGENT_ID,
        name: "Reviewer",
        role: "reviewer",
        scope: "project_local",
        tools: [],
      },
    ],
    mcp_servers: [],
  });

  await page.goto(`/admin/projects/${PROJECT_ID}/agent-tools-diagnostic`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId(`diagnostic-agent-card-${AGENT_ID}`)).toBeVisible();
  await expect(page.getByTestId(`diagnostic-agent-tools-empty-${AGENT_ID}`)).toBeVisible();
});
