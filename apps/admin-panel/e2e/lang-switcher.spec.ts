import { expect, test, type Page } from "@playwright/test";

import { navigateVia } from "./helpers/nav";

/**
 * E2E for the top header language switcher.
 *
 * Doctrine (CLAUDE.md §12): ES + EN únicamente. El selector vive en
 * el header global (visible en todas las pantallas) y empuja el
 * idioma al contexto, que consume cualquier pantalla que necesite
 * texto bilingüe del backend.
 *
 * Scope:
 *   1. El switcher está presente en cualquier pantalla `/admin/*`.
 *   2. ES está activo por defecto (sin localStorage previo).
 *   3. Cambiar a EN actualiza el `aria-pressed` y los snippets de
 *      prompt en la página de agentes (que sí pinta bilingüe).
 *   4. La preferencia persiste cruzando una recarga.
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

test("the lang switcher is visible on all admin screens and defaults to ES", async ({ page }) => {
  await login(page);

  // Dashboard.
  await expect(page.getByTestId("lang-switcher")).toBeVisible();
  await expect(page.getByTestId("lang-es")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("lang-en")).toHaveAttribute("aria-pressed", "false");

  // Navigate to a different screen; the switcher must still be there. El grupo
  // `recursos` de la nav arranca cerrado en el dashboard (ver `helpers/nav.ts`).
  await navigateVia(page, "recursos", "nav-agents");
  await expect(page).toHaveURL(/\/admin\/agents$/);
  await expect(page.getByTestId("lang-switcher")).toBeVisible();
});

test("switching to EN re-renders the agent prompts and persists across reload", async ({
  page,
  context,
}) => {
  // Start from a clean storage so the default ES is what we observe.
  await context.clearCookies();
  await login(page);
  await navigateVia(page, "recursos", "nav-agents");
  await expect(page).toHaveURL(/\/admin\/agents$/);

  // Wait for the agents grid to mount before snapshotting prompt locales.
  await expect(page.getByTestId("agents-grid")).toBeVisible();
  const promptBlocks = page.locator("[data-testid^=prompt-]");
  await expect(promptBlocks.first()).toHaveAttribute("data-lang", "es");

  // Flip to EN.
  await page.getByTestId("lang-en").click();
  await expect(page.getByTestId("lang-en")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("lang-es")).toHaveAttribute("aria-pressed", "false");
  await expect(promptBlocks.first()).toHaveAttribute("data-lang", "en");

  // Reload and assert the choice survives.
  await page.reload();
  await expect(page.getByTestId("lang-en")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-testid^=prompt-]").first()).toHaveAttribute("data-lang", "en");
});

test("the user menu in the header exposes a working logout", async ({ page }) => {
  await login(page);
  await page.getByTestId("user-menu").click();
  await expect(page.getByTestId("user-menu-popover")).toBeVisible();
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/login$/);
});
