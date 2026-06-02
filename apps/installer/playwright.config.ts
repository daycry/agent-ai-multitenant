import { defineConfig } from "@playwright/test";

/**
 * Playwright config for the temporary installer wizard.
 *
 * Auto-starts `npm run dev` on :3100 (a distinct port from the admin-panel's
 * :3000 so both can run side by side). The installer backend (FastAPI) is an
 * EXTERNAL dependency the test caller brings up if a spec needs it — the
 * Phase-A shell spec drives only the client-side state machine, so it needs
 * no backend.
 *
 * Env overrides:
 *   E2E_BASE_URL   where Playwright points the browser
 *                  (default: http://localhost:3100).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 90_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
