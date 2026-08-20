import { expect, test } from "@playwright/test";

import { navigateVia } from "./helpers/nav";

/**
 * E2E for the Agents Catalog screen (task_01_19).
 *
 * Pre-conditions (caller's responsibility, handled by run-e2e.ps1):
 *   - docker stack up.
 *   - api-server running with the seeds applied
 *     (`python -m api_server.seeds`) -- 11 built-in agents must exist.
 *   - admin user pre-seeded with credentials in E2E_ADMIN_* env vars.
 *   - admin-panel dev server on http://localhost:3000.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

async function loginAndGoToCatalog(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel(/^(password|contraseña)$/i).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);

  // Navegar por la nav (no por URL directa) para cubrir el camino feliz. El
  // grupo `recursos` arranca CERRADO estando en el dashboard, así que hay que
  // abrirlo antes: ver `helpers/nav.ts`.
  await navigateVia(page, "recursos", "nav-agents");
  await expect(page).toHaveURL(/\/admin\/agents$/);
}

test("catalog page loads and shows the three tabs", async ({ page }) => {
  await loginAndGoToCatalog(page);

  await expect(page.getByTestId("agents-tabs")).toBeVisible();
  await expect(page.getByTestId("tab-builtin")).toBeVisible();
  await expect(page.getByTestId("tab-template")).toBeVisible();
  await expect(page.getByTestId("tab-local")).toBeVisible();
});

test("built-in tab lists every seeded built-in agent", async ({ page }) => {
  await loginAndGoToCatalog(page);

  // The Built-in tab is the default (defaultValue="builtin").
  await expect(page.getByTestId("agents-grid")).toBeVisible();
  // `[data-testid^=agent-]` casa DOS nodos por tarjeta desde que cada una
  // envuelve su enlace en `agent-link-{id}`: 27 agentes daban 54 nodos. Mismo
  // patrón que el `:not([data-testid^=template-pick-])` que ya hubo que poner
  // en `project-wizard.spec.ts`.
  const cards = page
    .getByTestId("agents-grid")
    .locator("[data-testid^=agent-]:not([data-testid^=agent-link-])");
  // El número NO se fija a mano: la propia pestaña publica cuántos hay
  // («Built-in (27)»), y el invariante que importa es que el grid pinte TODAS
  // las filas que la API devolvió, ni una menos. Fijarlo a mano es lo que dejó
  // este spec afirmando 11 mucho después de que el catálogo built-in creciera a
  // 27 (los 11 del núcleo + `qa_e2e_automator` + 5 plantillas de agente humano
  // + los 10 del equipo CodeIgniter 4, que el seed añade por su cuenta
  // justamente para no tocar el conteo que fija `test_seed_agents`).
  const label = (await page.getByTestId("tab-builtin").textContent()) ?? "";
  const announced = Number(/\((\d+)\)/.exec(label)?.[1]);
  expect(announced, `la pestaña Built-in debe publicar un conteo: "${label}"`).toBeGreaterThan(0);
  await expect(cards).toHaveCount(announced);

  // Spot-check a couple of names from the seed.
  await expect(
    page.getByTestId("agents-grid").getByText("Project Manager", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByTestId("agents-grid").getByText("Code Reviewer", { exact: true }),
  ).toBeVisible();
});

test("switching to the local tab shows the empty-state copy", async ({ page }) => {
  await loginAndGoToCatalog(page);

  await page.getByTestId("tab-local").click();
  await expect(page.getByText(/No hay agentes locales de proyecto/i)).toBeVisible();
});
