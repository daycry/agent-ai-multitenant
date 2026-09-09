import { expect, test, type Page, type Route } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E (subset mockeado, sin backend) del botón «Importar» de la tarjeta de un
 * servidor MCP en /admin/projects/{id}/mcp-servers (ADR 0166, `task_mk_12`).
 *
 * Cubre:
 *   - la tarjeta avisa «sin importar» cuando el catálogo no tiene `<server>.*`;
 *   - «Importar» hace POST a `/mcp/servers/{name}/import-tools` SIN `tool_names`,
 *     pinta el resumen y, al invalidar el catálogo, la insignia pasa a
 *     «N tools importadas»;
 *   - `TOO_MANY_TOOLS` se traduce a la frase humana, no al código crudo.
 */
const PROJECT_ID = "11111111-0000-0000-0000-000000000001";
const SERVER = {
  name: "toy",
  transport: "stdio",
  command: "toy-mcp",
  args: [],
  env: {},
  url: null,
  headers: {},
  auth_ref: null,
  timeout_s: 30,
};

function importedTool(name: string, n: number) {
  return {
    id: `44444444-0000-0000-0000-00000000000${n}`,
    tenant_id: PROJECT_ID,
    name,
    description: null,
    category: "mcp",
    implementation_type: "mcp_tool",
    security_level: "sandboxed",
    is_builtin: false,
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function setup(page: Page): Promise<{ catalog: unknown[] }> {
  const state = { catalog: [] as unknown[] };
  await seedSession(page);
  await page.route(apiRoute(`/projects/${PROJECT_ID}`), (route) =>
    json(route, { id: PROJECT_ID, name: "Mediapro Internal", mcp_servers: [SERVER] }),
  );
  await page.route(apiRoute("/mcp-catalog"), (route) => json(route, []));
  await page.route(apiRoute("/tools**"), (route) => json(route, state.catalog));
  return state;
}

async function open(page: Page): Promise<void> {
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("mcp-server-card-toy")).toBeVisible();
}

test("Importar trae todas las tools anunciadas y la insignia pasa a importadas", async ({
  page,
}) => {
  const state = await setup(page);
  let importBody: unknown = undefined;
  await page.route(apiRoute(`/projects/${PROJECT_ID}/mcp/servers/toy/import-tools`), (route) => {
    importBody = route.request().postDataJSON();
    state.catalog = [importedTool("toy.echo", 1), importedTool("toy.add", 2)];
    return json(route, {
      tools: [{ name: "toy.echo" }, { name: "toy.add" }],
      retired: [],
      omitted: [],
      warnings: [],
    });
  });

  await open(page);
  await expect(page.getByTestId("mcp-server-not-imported-toy")).toBeVisible();

  await page.getByTestId("mcp-server-import-toy").click();

  await expect(page.getByTestId("mcp-server-import-result-toy")).toContainText(
    "Importadas 2 tools al catálogo",
  );
  // Sin `tool_names`: se importa TODO lo anunciado (ADR 0166 R1).
  expect(importBody).toEqual({});
  await expect(page.getByTestId("mcp-server-imported-toy")).toContainText("2 tools importadas");
  await expect(page.getByTestId("mcp-server-not-imported-toy")).toHaveCount(0);
});

test("TOO_MANY_TOOLS se explica en humano en la tarjeta", async ({ page }) => {
  await setup(page);
  await page.route(apiRoute(`/projects/${PROJECT_ID}/mcp/servers/toy/import-tools`), (route) =>
    json(
      route,
      { detail: { error_code: "TOO_MANY_TOOLS", message: "server announces 240 tools" } },
      422,
    ),
  );

  await open(page);
  await page.getByTestId("mcp-server-import-toy").click();

  const error = page.getByTestId("mcp-server-import-error-toy");
  await expect(error).toContainText("más de 200 tools");
  await expect(error).not.toContainText("TOO_MANY_TOOLS");
  await expect(page.getByTestId("mcp-server-import-result-toy")).toHaveCount(0);
});
