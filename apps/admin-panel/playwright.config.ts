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
  // One retry under CI: even against a pre-built `next start` server the first
  // hit to a route can be marginally slow on a cold runner (Plan prod-01 — the
  // mocked subset timed out under `next dev`; CI now builds + serves prod).
  retries: process.env.CI ? 1 : 0,
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
    // Configurable so CI can serve a PRE-BUILT production server
    // (E2E_WEBSERVER_CMD="npm run start" after `npm run build`) instead of
    // `next dev`. Under `next dev` Next compiles each route on first hit, and
    // on a CI runner that latency blew past the per-spec timeouts (Plan
    // prod-01: the mocked subset failed in CI). `next start` is pre-compiled.
    command: process.env.E2E_WEBSERVER_CMD ?? "npm run dev",
    url: "http://localhost:3000",
    // If you already have a server open in another terminal, reuse it instead
    // of failing or starting a second one.
    reuseExistingServer: !process.env.CI,
    // Boot slack (next dev compiles tailwind/types on first boot; next start
    // is fast). 120s is comfortable for either.
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
