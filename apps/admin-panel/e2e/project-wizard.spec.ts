import { expect, test, type Page } from "@playwright/test";

import { navigateVia } from "./helpers/nav";

/**
 * E2E for the project-from-template wizard (task_01_21).
 *
 * Runs visibly with:
 *   .\scripts\dev\run-e2e.ps1 -Headed -SlowMo 400 -Spec e2e\project-wizard.spec.ts
 *
 * Covers steps 1 + 2 of the wizard UX:
 *   - Step 1 lists the 8 built-in templates and lets the user pick.
 *   - Picking a template advances to step 2 and prefills the name
 *     (strips the "Plantilla: " prefix) plus shows a preview panel.
 *   - "Cambiar plantilla" goes back to step 1.
 *
 * Submission (POST /projects) is NOT exercised here for the same
 * tid-claim reason documented in team-detail.spec.ts; the visible
 * walkthrough still demonstrates the wizard's UX end-to-end.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel(/^(password|contraseña)$/i).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);
}

test("wizard step 1 lists every built-in template", async ({ page }) => {
  await login(page);
  // El grupo `recursos` de la nav arranca cerrado (ver `helpers/nav.ts`).
  await navigateVia(page, "recursos", "nav-projects");
  await expect(page).toHaveURL(/\/admin\/projects$/);

  // Click "Crear proyecto" -- works both from the header button and
  // from the empty-state CTA when no tenant projects exist.
  await page.getByTestId("new-project-button").click();
  await expect(page).toHaveURL(/\/admin\/projects\/new$/);

  // Step 1 visible. Exclude the inner template-pick-... buttons that share the
  // testid prefix.
  await expect(page.getByTestId("wizard-step-1")).toBeVisible();
  const allTemplateNodes = page
    .getByTestId("templates-grid")
    .locator("[data-testid^=template-]:not([data-testid^=template-pick-])");
  await expect(allTemplateNodes).not.toHaveCount(0);

  // El título decía 8 y el seed lleva 9 desde que se mergeó la plantilla
  // built-in de CodeIgniter 4 (PR #33): `BUILTIN_PROJECT_TEMPLATES` sigue
  // teniendo 8 —su test fija ese número— y `seed_ci4_project_template` añade la
  // novena por su cuenta, justamente para no tocarlo. Se comprueban las NUEVE
  // por nombre en vez de contarlas: una plantilla que desaparezca se ve aunque
  // otra entre a la vez, y esto no vuelve a caducar cuando el catálogo crezca.
  for (const name of [
    "Plantilla: API REST",
    "Plantilla: Webapp Full-Stack",
    "Plantilla: Data Pipeline",
    "Plantilla: Migración Legacy",
    "Plantilla: Investigación + Especificación",
    "Plantilla: DevOps Bootstrap",
    "Plantilla: Suite E2E",
    "Plantilla: Modernización de Documentación",
    "Plantilla: App CodeIgniter 4",
  ]) {
    await expect(
      page.getByTestId("templates-grid").getByText(name),
      `falta la plantilla built-in "${name}"`,
    ).toBeVisible();
  }
});

test("picking a template advances to step 2 with prefilled fields", async ({ page }) => {
  await login(page);
  await page.goto("/admin/projects/new");

  // Pick "Plantilla: API REST".
  const apiRestCard = page
    .getByTestId("templates-grid")
    .locator("[data-testid^=template-]:not([data-testid^=template-pick-])")
    .filter({ hasText: "Plantilla: API REST" });
  await apiRestCard.getByRole("button", { name: /usar plantilla/i }).click();

  // Step 2 visible.
  await expect(page.getByTestId("wizard-step-2")).toBeVisible();
  await expect(page.getByTestId("wizard-title")).toContainText("personaliza");

  // The "Plantilla: " prefix is stripped from the suggested name.
  const nameInput = page.getByTestId("wizard-name");
  await expect(nameInput).toHaveValue("API REST");

  // Preview panel shows the template's name.
  await expect(page.getByText("Plantilla: API REST")).toBeVisible();

  // "Cambiar plantilla" returns to step 1.
  await page.getByTestId("wizard-back").click();
  await expect(page.getByTestId("wizard-step-1")).toBeVisible();
});
