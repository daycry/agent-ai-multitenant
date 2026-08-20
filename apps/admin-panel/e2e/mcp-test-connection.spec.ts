import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Probar conexión" button inside the MCP server editor
 * dialog (Plan 05 task_05_07).
 *
 * The button lives in /admin/projects/{id}/mcp-servers; when pressed
 * it POSTs the current form state to
 * `/projects/{id}/mcp/test-connection`. The endpoint returns either
 * a DiscoveryResult or a typed McpTestConnectionError. The UI shows
 * the result (server name + tools list) or the error in an inline
 * panel below the form — never as a second modal.
 *
 * Mocks:
 *   - GET  /projects/{id}                  — empty server list so we
 *                                            can drive the add-flow
 *   - POST /projects/{id}/mcp/test-connection — variable per test
 */

const PROJECT_ID = "11111111-0000-0000-0000-000000000001";

async function loadPageWithStubbedProject(page: Page): Promise<void> {
  await seedSession(page);

  await page.route(`http://localhost:8001/projects/${PROJECT_ID}`, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: PROJECT_ID,
          name: "Mediapro Internal",
          mcp_servers: [],
        }),
      });
    }
    return route.continue();
  });
}

async function openCreateDialog(page: Page): Promise<void> {
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });
  // Espera a que la lista del proyecto haya RENDERIZADO en cliente antes de
  // pulsar (prod-16 `task_prod16_03`). `mcp-add-button` sale ya en el primer
  // render —está en la cabecera, antes de que resuelva la consulta—, así que
  // Playwright lo encontraba y lo pulsaba ANTES de que React hubiese
  // hidratado: el click no llegaba a ningún handler y el diálogo no se abría.
  // Bajo `next start` (precompilado) la hidratación gana la carrera y el spec
  // pasaba; bajo `next dev` en una máquina cargada, no. El estado vacío sí
  // depende de la consulta, así que esperarlo es esperar a la hidratación.
  await expect(page.getByTestId("project-mcp-empty")).toBeVisible();
  await page.getByTestId("mcp-add-button").click();
  await expect(page.getByTestId("mcp-server-dialog")).toBeVisible();

  // Minimum viable stdio config — enough to enable the Probar button.
  await page.getByTestId("mcp-form-name").fill("toy");
  await page.getByTestId("mcp-form-command").fill("toy-mcp");
}

// ---------------------------------------------------------------------------
// Success path
// ---------------------------------------------------------------------------
test("Probar shows server name + tools list on success", async ({ page }) => {
  await loadPageWithStubbedProject(page);
  await page.route(`http://localhost:8001/projects/${PROJECT_ID}/mcp/test-connection`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        server_name: "toy-mcp-server",
        server_version: "1.0.0",
        server_instructions: null,
        tools: [
          { name: "echo", description: "Echo input.", input_schema: {} },
          { name: "add", description: null, input_schema: {} },
        ],
      }),
    }),
  );

  await openCreateDialog(page);
  await page.getByTestId("mcp-form-test").click();

  await expect(page.getByTestId("mcp-form-test-result")).toBeVisible();
  await expect(page.getByTestId("mcp-form-test-server-name")).toHaveText("toy-mcp-server");
  await expect(page.getByTestId("mcp-form-test-server-version")).toHaveText("1.0.0");
  await expect(page.getByTestId("mcp-form-test-tool-count")).toHaveText("2");
  await expect(page.getByTestId("mcp-form-test-tool-echo")).toBeVisible();
  await expect(page.getByTestId("mcp-form-test-tool-add")).toBeVisible();
});

// ---------------------------------------------------------------------------
// AUTH_ERROR — typed code surfaces as a readable message
// ---------------------------------------------------------------------------
test("Probar surfaces AUTH_ERROR when the resolver rejects", async ({ page }) => {
  await loadPageWithStubbedProject(page);
  await page.route(`http://localhost:8001/projects/${PROJECT_ID}/mcp/test-connection`, (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          error_code: "AUTH_ERROR",
          message:
            "server 'toy' declares auth_ref='vault:secret/data/x' but no VaultResolver was supplied",
        },
      }),
    }),
  );

  await openCreateDialog(page);
  await page.getByTestId("mcp-form-test").click();

  await expect(page.getByTestId("mcp-form-test-error")).toBeVisible();
  await expect(page.getByTestId("mcp-form-test-error")).toContainText("AUTH_ERROR");

  // Success panel must NOT appear when an error came back.
  await expect(page.getByTestId("mcp-form-test-result")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// TRANSPORT_ERROR — typed code surfaces, no success panel
// ---------------------------------------------------------------------------
test("Probar surfaces TRANSPORT_ERROR on a 502", async ({ page }) => {
  await loadPageWithStubbedProject(page);
  await page.route(`http://localhost:8001/projects/${PROJECT_ID}/mcp/test-connection`, (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          error_code: "TRANSPORT_ERROR",
          message: "connection refused: localhost:8123",
        },
      }),
    }),
  );

  await openCreateDialog(page);
  await page.getByTestId("mcp-form-test").click();

  await expect(page.getByTestId("mcp-form-test-error")).toBeVisible();
  await expect(page.getByTestId("mcp-form-test-error")).toContainText("TRANSPORT_ERROR");
  await expect(page.getByTestId("mcp-form-test-result")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Probar is disabled when the form is empty
// ---------------------------------------------------------------------------
test("Probar button is disabled until name is filled", async ({ page }) => {
  await loadPageWithStubbedProject(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/mcp-servers`, {
    waitUntil: "domcontentloaded",
  });
  // Espera a que la lista del proyecto haya RENDERIZADO en cliente antes de
  // pulsar (prod-16 `task_prod16_03`). `mcp-add-button` sale ya en el primer
  // render —está en la cabecera, antes de que resuelva la consulta—, así que
  // Playwright lo encontraba y lo pulsaba ANTES de que React hubiese
  // hidratado: el click no llegaba a ningún handler y el diálogo no se abría.
  // Bajo `next start` (precompilado) la hidratación gana la carrera y el spec
  // pasaba; bajo `next dev` en una máquina cargada, no. El estado vacío sí
  // depende de la consulta, así que esperarlo es esperar a la hidratación.
  await expect(page.getByTestId("project-mcp-empty")).toBeVisible();
  await page.getByTestId("mcp-add-button").click();

  // Fresh form: name empty → button disabled.
  await expect(page.getByTestId("mcp-form-test")).toBeDisabled();

  await page.getByTestId("mcp-form-name").fill("x");
  await expect(page.getByTestId("mcp-form-test")).toBeEnabled();
});
