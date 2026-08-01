import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E del despliegue del marketplace (ADR 0142, `task_mkt2_08`).
 *
 * Cubre el viaje de `human_mkt2_01` en la parte que un navegador puede hacer
 * con la red mockeada: desplegar la misma instalación en DOS proyectos con
 * `base_url` distinta —el caso que el modelo viejo no sabía expresar—, ver la
 * activación local desde la pestaña MCP, y retirar de uno comprobando que el
 * otro queda intacto.
 *
 * **Escrita, tipada y lintada; NO ejecutada aquí**: la suite de Playwright pide
 * navegador y el entorno de esta fase no lo tiene. Lo que sí se garantiza es que
 * los `data-testid` que usa son los que los componentes emiten de verdad —los
 * mismos que afirman sus tests de vitest—, así que si alguien los renombra, la
 * suite unitaria cae antes que ésta.
 *
 * Lo que queda para el test HUMANO y no se puede mockear: publicar de verdad,
 * aprobar como system admin y completar el OAuth del ADR 0127 en el proveedor.
 */

const INSTALLATION_ID = "inst-e2e-1";
const LISTING_ID = "listing-e2e-1";
const PROJECT_A = "proj-e2e-a";
const PROJECT_B = "proj-e2e-b";

const LISTING = {
  id: LISTING_ID,
  source_id: "src-1",
  tenant_id: null,
  kind: "mcp_server",
  name: "Jira MCP",
  version: "1.2.0",
  description: "Issues y sprints",
  author: "plataforma",
  trust_level: "verified",
  review_status: "published",
  manifest: {
    targets: ["backend_dev"],
    mcp_server: { name: "jira", transport: "streamable_http" },
    config_schema: {
      properties: {
        base_url: { type: "string", title: "Base URL", default: null },
        timeout_ms: { type: "integer", title: "Timeout (ms)", default: 30000, minimum: 1 },
      },
      required: [],
    },
  },
  requested_permissions: [],
  is_signed: false,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const PROJECTS = [
  { id: PROJECT_A, name: "App A" },
  { id: PROJECT_B, name: "App B" },
];

function deploymentRow(id: string, projectId: string, baseUrl: string, status = "active") {
  return {
    id,
    tenant_id: "tenant-1",
    installation_id: INSTALLATION_ID,
    project_id: projectId,
    config: { base_url: baseUrl, timeout_ms: 30000 },
    role_map: { "*": ["backend_dev"] },
    deployed_version: "1.2.0",
    status,
    created_refs: { mcp_servers: ["jira"] },
    deployed_by: null,
    retired_at: status === "retired" ? "2026-08-01T10:00:00Z" : null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

interface DeployCall {
  installationId: string;
  body: { project_id: string; config: Record<string, unknown>; role_map: string[] };
}

/** Mocks del estado servidor. `deployments` es mutable: el POST lo va llenando. */
async function setup(
  page: Page,
  opts: {
    deployments?: ReturnType<typeof deploymentRow>[];
    onDeploy?: (call: DeployCall) => void;
  } = {},
): Promise<{ retired: string[] }> {
  const deployments = opts.deployments ?? [];
  const retired: string[] = [];

  await seedSession(page);

  await page.route(`**/api/marketplace/installations/${INSTALLATION_ID}/permissions`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        installation_id: INSTALLATION_ID,
        listing_id: LISTING_ID,
        status: "enabled",
        consent_required: false,
        all_granted: true,
        permissions: [],
      }),
    }),
  );

  await page.route(`**/api/marketplace/listings/${LISTING_ID}`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(LISTING) }),
  );

  await page.route(
    `**/api/marketplace/installations/${INSTALLATION_ID}/deployments`,
    async (route) => {
      if (route.request().method() === "POST") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        opts.onDeploy?.({ installationId: INSTALLATION_ID, body });
        const row = deploymentRow(
          `dep-${deployments.length + 1}`,
          String(body.project_id),
          String(body.config?.base_url ?? ""),
        );
        deployments.push(row);
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            deployment: row,
            already_deployed: false,
            warnings: [],
            oauth_pending: false,
          }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(deployments),
      });
    },
  );

  await page.route("**/api/marketplace/deployments/*/retire", async (route) => {
    const id = route.request().url().split("/deployments/")[1].split("/")[0];
    retired.push(id);
    const row = deployments.find((d) => d.id === id);
    if (row) row.status = "retired";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ deployment_id: id, status: "retired", removed_refs: 1 }),
    });
  });

  await page.route("**/api/projects", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PROJECTS) }),
  );

  await page.route(`**/api/projects/${PROJECT_A}/marketplace/available`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) }),
  );

  return { retired };
}

test("la ficha despliega la misma instalación en dos proyectos con base_url distinta", async ({
  page,
}) => {
  const calls: DeployCall[] = [];
  await setup(page, { onDeploy: (call) => calls.push(call) });

  await page.goto(`/admin/marketplace/installations/${INSTALLATION_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("deployments-empty")).toBeVisible();

  // --- proyecto A ---------------------------------------------------------
  await page.getByTestId("deployments-deploy-open").click();
  await page.getByTestId(`deployments-project-${PROJECT_A}`).check();
  await expect(page.getByTestId(`deploy-${PROJECT_A}-form`)).toBeVisible();
  await page.getByTestId(`deploy-${PROJECT_A}-field-base_url`).fill("https://a.example");
  await page.getByTestId("deployments-deploy-submit").click();
  await expect(page.getByTestId(`deploy-result-${PROJECT_A}`)).toHaveAttribute(
    "data-outcome",
    "ok",
  );

  // --- proyecto B, con OTRA base_url --------------------------------------
  await page.getByTestId("deployments-deploy-open").click();
  await page.getByTestId(`deployments-project-${PROJECT_B}`).check();
  await page.getByTestId(`deploy-${PROJECT_B}-field-base_url`).fill("https://b.example");
  await page.getByTestId("deployments-deploy-submit").click();
  await expect(page.getByTestId(`deploy-result-${PROJECT_B}`)).toHaveAttribute(
    "data-outcome",
    "ok",
  );

  expect(calls.map((c) => c.body.config.base_url)).toEqual([
    "https://a.example",
    "https://b.example",
  ]);
  expect(calls[0].body.role_map).toEqual(["backend_dev"]);
});

test("un proyecto ya desplegado no se puede volver a marcar", async ({ page }) => {
  await setup(page, { deployments: [deploymentRow("dep-1", PROJECT_A, "https://a.example")] });

  await page.goto(`/admin/marketplace/installations/${INSTALLATION_ID}`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("deployment-dep-1")).toContainText("App A");

  await page.getByTestId("deployments-deploy-open").click();
  await expect(page.getByTestId(`deployments-project-${PROJECT_A}`)).toBeDisabled();
  await expect(page.getByTestId(`deployments-project-${PROJECT_B}`)).toBeEnabled();
});

test("retirar uno deja el otro intacto", async ({ page }) => {
  const { retired } = await setup(page, {
    deployments: [
      deploymentRow("dep-1", PROJECT_A, "https://a.example"),
      deploymentRow("dep-2", PROJECT_B, "https://b.example"),
    ],
  });

  await page.goto(`/admin/marketplace/installations/${INSTALLATION_ID}`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("deployment-retire-dep-1").click();
  await page.getByTestId("confirm-dialog-accept").click();

  await expect(page.getByTestId("deployment-retire-dep-2")).toBeVisible();
  expect(retired).toEqual(["dep-1"]);
});

test("la pestaña MCP del proyecto ofrece activar lo disponible del tenant", async ({ page }) => {
  const calls: DeployCall[] = [];
  await setup(page, { onDeploy: (call) => calls.push(call) });

  await page.route(`**/api/projects/${PROJECT_A}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: PROJECT_A, name: "App A", mcp_servers: [], mcp_tool_roles: {} }),
    }),
  );
  await page.route("**/api/mcp-catalog", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.unroute(`**/api/projects/${PROJECT_A}/marketplace/available`);
  await page.route(`**/api/projects/${PROJECT_A}/marketplace/available`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          installation_id: INSTALLATION_ID,
          listing_id: LISTING_ID,
          kind: "mcp_server",
          name: "Jira MCP",
          version: "1.2.0",
          description: null,
          trust_level: "verified",
          config_schema: LISTING.manifest.config_schema,
          targets: ["backend_dev"],
        },
      ]),
    }),
  );

  await page.goto(`/admin/projects/${PROJECT_A}/mcp-servers`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId(`available-${INSTALLATION_ID}`)).toBeVisible();
  await page.getByTestId(`available-activate-${INSTALLATION_ID}`).click();
  await page.getByTestId(`available-${INSTALLATION_ID}-field-base_url`).fill("https://a.example");
  await page.getByTestId(`available-submit-${INSTALLATION_ID}`).click();

  await expect.poll(() => calls.length).toBeGreaterThan(0);
  expect(calls[0].body.project_id).toBe(PROJECT_A);
});
