import { expect, test, type Page } from "@playwright/test";

import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Configurar Política de Validación Humana" screen
 * (task_01_23).
 *
 * Visible run:
 *   npx playwright test e2e/approval-policy.spec.ts
 *
 * Coverage:
 *   1. Nav-item lands on /admin/approval-policy and lista los 4 presets
 *      built-in en el orden en que llegan.
 *   2. Switching the active preset re-renders the 13-category table
 *      with the matching baseline decisions (Sandbox = all auto;
 *      Cliente Externo = all humano).
 *   3. Toggling a category creates a visible override badge and the
 *      "Cambios sin guardar" indicator appears.
 *   4. With zero tenant projects, the "Aplicar política" button is
 *      disabled and the empty-state hint shows.
 *
 * ---------------------------------------------------------------------------
 * 2026-08-19 — MIGRADO a sesión sembrada + mocks. Por qué, y por qué no es
 * rebajar el listón:
 *
 * El spec hacía LOGIN REAL contra el api-server. CI lo mete igualmente en el
 * subset "mockeado" (lo barre `grep -rlE "page.route"` porque el cuarto test
 * mockeaba `/projects`), y allí no hay backend: los cuatro tests morían en
 * `toHaveURL(/admin/dashboard)` sin haber llegado a mirar la pantalla. Un test
 * que sólo puede fallar no vigila nada.
 *
 * La parte que SÍ necesitaba base de datos —que la semilla cree exactamente
 * cuatro políticas built-in, que Sandbox sea todo `auto` y Cliente Externo todo
 * `human_required`, y que estén las 13 categorías— ya la cubre
 * `tests/integration/test_seed_policies.py` (5 tests), que es donde le toca:
 * comprueba la MIGRACIÓN, no la pantalla. Lo que queda aquí es lo que sólo se
 * puede comprobar en un navegador: que la tabla se repinta al cambiar de
 * preset, que un toggle marca override y ensucia el formulario, y que sin
 * proyecto no se puede aplicar.
 */

/** Las 13 categorías del spec §7.7-7.8, en el orden en que las pinta la pantalla. */
const CATEGORIES = [
  "code_changes",
  "git_commit",
  "git_push",
  "external_http_get",
  "external_http_post",
  "secrets_access",
  "data_migration",
  "production_deploy",
  "infra_provision",
  "secret_rotation",
  "external_communication",
  "data_export_pii",
  "user_management",
];

type Decision = "auto" | "human_required";

const allCategories = (decision: Decision): Record<string, Decision> =>
  Object.fromEntries(CATEGORIES.map((c) => [c, decision]));

/**
 * Los cuatro presets built-in, con la forma de `GET /approval-policies?builtin_only=true`.
 * Sandbox y Cliente Externo son los extremos que el test 2 usa como contraste.
 */
const PRESETS = [
  {
    id: "aaaa0000-0000-0000-0000-000000000001",
    name: "Sandbox",
    description: "Todo automático.",
    is_builtin: true,
    categories: { preset: "sandbox", categories: allCategories("auto") },
  },
  {
    id: "aaaa0000-0000-0000-0000-000000000002",
    name: "Desarrollo",
    description: "Equilibrio para el día a día.",
    is_builtin: true,
    categories: {
      preset: "development",
      categories: { ...allCategories("auto"), git_push: "human_required" as Decision },
    },
  },
  {
    id: "aaaa0000-0000-0000-0000-000000000003",
    name: "Producción",
    description: "Lo sensible pasa por un humano.",
    is_builtin: true,
    categories: { preset: "production", categories: allCategories("human_required") },
  },
  {
    id: "aaaa0000-0000-0000-0000-000000000004",
    name: "Cliente Externo",
    description: "Todo pasa por un humano.",
    is_builtin: true,
    categories: { preset: "customer-external", categories: allCategories("human_required") },
  },
];

const PROJECT = {
  id: "bbbb0000-0000-0000-0000-000000000001",
  name: "Proyecto A",
  is_template: false,
  human_approval_policy: null,
};

/** Prefijo de la clave que persiste abierto/cerrado cada grupo del menú. */
const NAV_GROUP_LS_PREFIX = "agentic.nav.group.";

async function setup(page: Page, opts: { projects?: object[] } = {}): Promise<void> {
  await seedSession(page);
  // La entrada del menú vive en el grupo "config-tenant", que arranca colapsado
  // fuera de sus rutas: sin esto el enlace ni siquiera está en el DOM.
  await page.addInitScript(
    ([key]) => window.localStorage.setItem(key, "1"),
    [NAV_GROUP_LS_PREFIX + "config-tenant"],
  );
  await page.route(apiRoute("/approval-policies?builtin_only=true"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PRESETS),
    }),
  );
  await page.route(apiRoute("/projects"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(opts.projects ?? [PROJECT]),
    }),
  );
}

test("nav opens the screen and lists the 4 built-in presets", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/dashboard", { waitUntil: "domcontentloaded" });
  await page.getByTestId("nav-approval-policy").click();
  await expect(page).toHaveURL(/\/admin\/approval-policy$/);

  await expect(page.getByTestId("presets-grid")).toBeVisible();
  const cards = page.getByTestId("presets-grid").locator("[data-testid^=preset-]");
  await expect(cards).toHaveCount(4);

  // Name spot-checks (seeded slugs). Scope to the presets grid because
  // the active preset's name also appears in the "Plantilla base:" line.
  const grid = page.getByTestId("presets-grid");
  await expect(grid.getByText("Sandbox", { exact: true })).toBeVisible();
  await expect(grid.getByText("Desarrollo", { exact: true })).toBeVisible();
  await expect(grid.getByText("Producción", { exact: true })).toBeVisible();
  await expect(grid.getByText("Cliente Externo", { exact: true })).toBeVisible();
});

test("switching presets re-renders the category baseline", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/approval-policy", { waitUntil: "domcontentloaded" });

  // Click Sandbox explicitly: its baseline is "everything auto", which
  // is a clean check independent of whichever preset the page
  // auto-selected on load (the order depends on seed created_at).
  await page
    .getByTestId("presets-grid")
    .locator("[data-testid^=preset-]")
    .filter({ has: page.getByRole("heading", { name: "Sandbox" }) })
    .click();

  const codeChanges = page.getByTestId("category-code_changes");
  await expect(codeChanges).toBeVisible();
  await expect(codeChanges).toHaveAttribute("data-decision", "auto");
  await expect(page.getByTestId("category-git_push")).toHaveAttribute("data-decision", "auto");

  // Switch to Cliente Externo → everything becomes human_required.
  await page
    .getByTestId("presets-grid")
    .locator("[data-testid^=preset-]")
    .filter({ has: page.getByRole("heading", { name: "Cliente Externo" }) })
    .click();

  await expect(codeChanges).toHaveAttribute("data-decision", "human_required");
  await expect(page.getByTestId("category-data_export_pii")).toHaveAttribute(
    "data-decision",
    "human_required",
  );
});

test("toggling a category marks it as override and surfaces the dirty badge", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/approval-policy", { waitUntil: "domcontentloaded" });

  // Switch to Producción so baseline is human_required and an override
  // to "auto" is meaningful.
  await page
    .getByTestId("presets-grid")
    .locator("[data-testid^=preset-]")
    .filter({ has: page.getByRole("heading", { name: "Producción" }) })
    .click();

  const target = page.getByTestId("category-git_push");
  await expect(target).toHaveAttribute("data-decision", "human_required");
  await expect(target).toHaveAttribute("data-override", "false");

  await page.getByTestId("toggle-git_push").click();
  await expect(target).toHaveAttribute("data-decision", "auto");
  await expect(target).toHaveAttribute("data-override", "true");
  await expect(page.getByTestId("override-git_push")).toBeVisible();
  await expect(page.getByTestId("dirty-badge")).toBeVisible();
});

test("save is disabled without a project", async ({ page }) => {
  // Sin proyectos del tenant no hay a qué aplicar la política.
  await setup(page, { projects: [] });
  await page.goto("/admin/approval-policy", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("no-projects-hint")).toBeVisible();
  await expect(page.getByTestId("save-policy")).toBeDisabled();
});
