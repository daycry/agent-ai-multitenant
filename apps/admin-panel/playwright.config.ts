import { defineConfig } from "@playwright/test";

/**
 * Phase-0 Playwright config.
 *
 * The web server is NOT started automatically — bring the admin-panel
 * dev server up manually (`npm run dev`) plus the api-server stack
 * (`docker compose up -d` from the repo root). Local config keeps the
 * test fast; CI will start everything itself in a later phase.
 *
 * E2E_BASE_URL overrides where Playwright points the browser.
 * E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD let the spec authenticate.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
