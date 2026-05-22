import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the "Configurar Política de Validación Humana" screen
 * (task_01_23).
 *
 * Visible run:
 *   .\scripts\dev\run-e2e.ps1 -Headed -SlowMo 600 -Spec e2e\approval-policy.spec.ts
 *
 * Coverage:
 *   1. Nav-item lands on /admin/approval-policy and lists the 4 seeded
 *      built-in presets in the order seeded.
 *   2. Switching the active preset re-renders the 13-category table
 *      with the matching baseline decisions (Sandbox = all auto;
 *      Cliente Externo = all humano).
 *   3. Toggling a category creates a visible override badge and the
 *      "Cambios sin guardar" indicator appears.
 *   4. With zero tenant projects, the "Aplicar política" button is
 *      disabled and the empty-state hint shows.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);
}

test("nav opens the screen and lists the 4 built-in presets", async ({ page }) => {
  await login(page);
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
  await login(page);
  await page.goto("/admin/approval-policy");

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
  await login(page);
  await page.goto("/admin/approval-policy");

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
  // Same rationale as in dual-kanban's empty-state test: the
  // superadmin portfolio sees projects from every tenant on the
  // persistent dev DB, so we mock /projects to [] to exercise the
  // empty-state path deterministically.
  await page.route("**/projects", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  await login(page);
  await page.goto("/admin/approval-policy");

  await expect(page.getByTestId("no-projects-hint")).toBeVisible();
  await expect(page.getByTestId("save-policy")).toBeDisabled();
});
