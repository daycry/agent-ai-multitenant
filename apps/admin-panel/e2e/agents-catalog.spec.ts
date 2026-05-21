import { expect, test } from "@playwright/test";

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
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);

  // Navigate via the dashboard's Agentes button so we cover the
  // happy-path navigation, not just a direct URL.
  await page.getByTestId("nav-agents").click();
  await expect(page).toHaveURL(/\/admin\/agents$/);
}

test("catalog page loads and shows the three tabs", async ({ page }) => {
  await loginAndGoToCatalog(page);

  await expect(page.getByTestId("agents-tabs")).toBeVisible();
  await expect(page.getByTestId("tab-builtin")).toBeVisible();
  await expect(page.getByTestId("tab-template")).toBeVisible();
  await expect(page.getByTestId("tab-local")).toBeVisible();
});

test("built-in tab lists the 11 seeded agents", async ({ page }) => {
  await loginAndGoToCatalog(page);

  // The Built-in tab is the default (defaultValue="builtin"). The grid
  // should be visible and contain 11 cards.
  await expect(page.getByTestId("agents-grid")).toBeVisible();
  const cards = page.getByTestId("agents-grid").locator("[data-testid^=agent-]");
  await expect(cards).toHaveCount(11);

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
