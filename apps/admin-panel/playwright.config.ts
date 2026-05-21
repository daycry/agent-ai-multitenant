import { defineConfig } from "@playwright/test";

/**
 * Phase-0 Playwright config.
 *
 * Auto-starts `npm run dev` on :3000 unless one is already running
 * (handy: keeps your own dev session if you have it open). Does NOT
 * start the api-server or the docker stack — those are external
 * dependencies the test caller must bring up:
 *
 *   docker compose -f docker/docker-compose.yml \
 *                  -f docker/docker-compose.dev.yml up -d
 *   uvicorn api_server.main:app --port 8001   # in apps/api-server
 *   (seed a System Admin user: see docs/02-getting-started/03-first-run.md)
 *
 * Env overrides:
 *   E2E_BASE_URL          where Playwright points the browser
 *                         (default: http://localhost:3000).
 *   E2E_ADMIN_EMAIL       login email   (default: root@example.com).
 *   E2E_ADMIN_PASSWORD    login password (default: longenoughpw).
 *   E2E_SLOW_MO           Delay (ms) between actions when headed.
 *                         Playwright has no --slow-mo CLI flag, so the
 *                         wrapper script (scripts/dev/run-e2e.ps1
 *                         -SlowMo N) sets this env var which we apply
 *                         via launchOptions.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // Force a single worker. With Next dev compiling pages on demand,
  // parallel workers race for compile time and a first-time navigation
  // can blow past the 5 s default toHaveURL timeout. Serial keeps the
  // suite small-and-fast and predictable.
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      slowMo: Number(process.env.E2E_SLOW_MO ?? 0),
    },
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    // If you already have `npm run dev` open in another terminal,
    // reuse it instead of failing or starting a second one.
    reuseExistingServer: !process.env.CI,
    // First boot of Next.js dev mode can take a moment (compiling
    // tailwind, type-checking pages). 90s leaves slack.
    timeout: 90_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
