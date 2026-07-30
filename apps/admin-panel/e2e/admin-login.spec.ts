import { expect, test } from "@playwright/test";

/**
 * Phase-0 happy path: login as a pre-seeded System Admin, land on the
 * dashboard, see at least the postgres service card.
 *
 * Pre-conditions (caller's responsibility):
 *   - docker compose stack is up.
 *   - api-server is running on http://localhost:8001 with CORS allowing
 *     http://localhost:3000.
 *   - There is a User row with is_system_admin=true matching the
 *     E2E_ADMIN_* env vars (defaults: root@example.com / longenoughpw).
 *   - admin-panel dev server is running on http://localhost:3000.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

test("login + dashboard happy path", async ({ page }) => {
  await page.goto("/login");

  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel(/^(password|contraseña)$/i).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();

  await expect(page).toHaveURL(/\/admin\/dashboard$/);
  await expect(page.getByTestId("services-grid")).toBeVisible();
  await expect(page.getByTestId("service-postgres")).toBeVisible();
});

test("wrong password shows inline error", async ({ page }) => {
  await page.goto("/login");

  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel(/^(password|contraseña)$/i).fill("definitely-wrong");
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();

  await expect(page.getByTestId("login-error")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});
