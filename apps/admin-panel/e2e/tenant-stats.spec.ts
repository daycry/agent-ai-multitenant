import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the tenant STATISTICS dashboard + runs explorer (Plan 14 task_14_12).
 *
 * A tenant_admin views how its agents perform and what it consumes: success
 * rate / mean time / mean cost, top/bottom agents, the daily trend, the
 * consumption summary and a filterable runs explorer. The page is
 * `<RoleGuard min="tenant_admin">`; the data comes from three tenant-scoped
 * (RLS) endpoints over the executions table.
 *
 * Mocks:
 *   - GET /me                          — identity (admin vs plain user)
 *   - GET /tenant-stats/dashboard      — agent statistics
 *   - GET /tenant-stats/consumption    — consumption summary
 *   - GET /tenant-stats/runs           — runs explorer (filters echoed back)
 *
 * Drives:
 *   - a plain user does NOT see the dashboard (RoleGuard fallback),
 *   - admin sees headline success rate + top/bottom agents + per-agent table +
 *     consumption summary + the runs explorer table,
 *   - the window selector re-queries with the chosen window_days,
 *   - a runs filter (verdict) re-queries with the chosen filter,
 *   - the currency toggle (Plan 11.1) re-queries with display_currency and shows
 *     the per-run converted cost column,
 *   - the currency note explains USD-canonical + display-only conversion.
 *
 * NOTE: written, not run (task_14_12 e2e is pending human verification — there
 * is no browser in the implementing environment).
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const AGENT_BE = "a1111111-0000-0000-0000-000000000010";
const AGENT_FE = "a1111111-0000-0000-0000-000000000011";
const EXEC_ID = "e1111111-0000-0000-0000-000000000040";
const BASE = "http://localhost:8001";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@platform.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Acme", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const TENANT_USER = {
  ...TENANT_ADMIN,
  user_id: "88888888-0000-0000-0000-000000000088",
  email: "user@platform.test",
  full_name: "Plain User",
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Acme", role: "tenant_user", is_active: true },
  ],
};

const AGENT_BE_STATS = {
  agent_id: AGENT_BE,
  agent_name: "Backend",
  agent_role: "backend",
  run_count: 3,
  succeeded: 2,
  success_rate: "0.667",
  mean_duration_ms: "2000.00",
  mean_cost_usd: "0.023333",
  total_cost_usd: "0.070000",
  total_tokens: 3500,
};

const AGENT_FE_STATS = {
  agent_id: AGENT_FE,
  agent_name: "Frontend",
  agent_role: "frontend",
  run_count: 1,
  succeeded: 1,
  success_rate: "1.000",
  mean_duration_ms: "2000.00",
  mean_cost_usd: "0.005000",
  total_cost_usd: "0.005000",
  total_tokens: 300,
};

function makeDashboard(windowDays: number) {
  return {
    window_days: windowDays,
    currency: "USD",
    total_runs: 4,
    succeeded_runs: 3,
    overall_success_rate: "0.750",
    mean_duration_ms: "2000.00",
    mean_cost_usd: "0.018750",
    total_cost_usd: "0.075000",
    by_agent: [AGENT_BE_STATS, AGENT_FE_STATS],
    top_agents: [AGENT_FE_STATS, AGENT_BE_STATS],
    bottom_agents: [AGENT_BE_STATS, AGENT_FE_STATS],
    trend: [
      {
        day: "2026-05-29",
        run_count: 2,
        succeeded: 1,
        success_rate: "0.500",
        total_cost_usd: "0.030000",
      },
      {
        day: "2026-05-30",
        run_count: 2,
        succeeded: 2,
        success_rate: "1.000",
        total_cost_usd: "0.045000",
      },
    ],
  };
}

const CONSUMPTION = {
  window_days: 90,
  currency: "USD",
  run_count: 4,
  accumulated_cost_usd: "0.075000",
  mean_cost_usd: "0.018750",
  total_tokens: 4500,
  total_tokens_input: 3000,
  total_tokens_output: 1500,
  total_tokens_cached: 0,
  costliest_run: {
    execution_id: EXEC_ID,
    task_id: "70000000-0000-0000-0000-000000000001",
    task_title: "Build login",
    agent_name: "Backend",
    total_cost_usd: "0.040000",
    total_tokens: 2000,
    created_at: "2026-05-30T10:00:00Z",
  },
};

function makeRun(verdict: string, displayCurrency = "") {
  // FX is display-only: USD is canonical and unchanged; when a non-USD display
  // currency is requested the backend adds the converted amount + applied rate
  // (here 1 USD = 0.92 EUR on the run's date → 0.050000 USD = 0.05 EUR).
  const converted = displayCurrency && displayCurrency !== "USD";
  return [
    {
      id: EXEC_ID,
      created_at: "2026-05-30T10:00:00Z",
      task_id: "70000000-0000-0000-0000-000000000001",
      task_title: "Build login",
      plan_id: "60000000-0000-0000-0000-000000000001",
      plan_title: "Plan A",
      agent_id: AGENT_BE,
      agent_name: "Backend",
      agent_role: "backend",
      model: "claude-opus",
      verdict,
      succeeded: verdict === "done",
      retry_count: 2,
      duration_ms: 1234,
      total_tokens: 1500,
      total_cost_usd: "0.050000",
      display_currency: converted ? displayCurrency : null,
      display_cost: converted ? "0.05" : null,
      applied_rate: converted ? "0.9200000000" : null,
      applied_rate_date: converted ? "2026-05-30" : null,
      started_at: "2026-05-30T10:00:00Z",
      completed_at: "2026-05-30T10:00:01Z",
    },
  ];
}

interface Capture {
  windows: number[];
  verdicts: string[];
  currencies: string[];
}

async function setup(
  page: Page,
  identity: typeof TENANT_ADMIN | typeof TENANT_USER = TENANT_ADMIN,
): Promise<Capture> {
  const capture: Capture = { windows: [], verdicts: [], currencies: [] };

  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route(`${BASE}/me`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(identity) }),
  );

  await page.route(`${BASE}/tenant-stats/dashboard**`, (route) => {
    const url = new URL(route.request().url());
    const windowDays = Number(url.searchParams.get("window_days") ?? "90");
    capture.windows.push(windowDays);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeDashboard(windowDays)),
    });
  });

  await page.route(`${BASE}/tenant-stats/consumption**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CONSUMPTION),
    }),
  );

  await page.route(`${BASE}/tenant-stats/runs**`, (route) => {
    const url = new URL(route.request().url());
    const verdict = url.searchParams.get("verdict") ?? "";
    if (verdict) capture.verdicts.push(verdict);
    const currency = url.searchParams.get("display_currency") ?? "";
    if (currency) capture.currencies.push(currency);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeRun(verdict || "done", currency)),
    });
  });

  return capture;
}

// ---------------------------------------------------------------------------
// RBAC — a plain user does NOT see the dashboard
// ---------------------------------------------------------------------------
test("a plain user does not see the tenant stats dashboard", async ({ page }) => {
  await setup(page, TENANT_USER);
  await page.goto("/admin/tenant-stats", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("tenant-stats-page")).toBeVisible();
  await expect(page.getByTestId("tenant-stats-dashboard")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Admin sees headline + agents + consumption + runs explorer
// ---------------------------------------------------------------------------
test("admin sees the stats, consumption summary and runs explorer", async ({ page }) => {
  await setup(page, TENANT_ADMIN);
  await page.goto("/admin/tenant-stats", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("tenant-stats-dashboard")).toBeVisible();
  await expect(page.getByTestId("overall-success-rate")).toContainText("75.0%");
  await expect(page.getByTestId("total-runs")).toContainText("4");
  await expect(page.getByTestId("mean-cost")).toContainText("$0.018750");

  // top / bottom agents.
  await expect(page.getByTestId(`top-agent-${AGENT_FE}`)).toContainText("Frontend");
  await expect(page.getByTestId(`top-agent-${AGENT_FE}`)).toContainText("100.0%");
  await expect(page.getByTestId(`bottom-agent-${AGENT_BE}`)).toContainText("Backend");

  // per-agent table.
  await expect(page.getByTestId(`agent-row-${AGENT_BE}`)).toContainText("66.7%");

  // consumption summary.
  await expect(page.getByTestId("accumulated-cost")).toContainText("$0.075000");
  await expect(page.getByTestId("consumption-tokens")).toContainText("3000/1500/0");
  await expect(page.getByTestId("costliest-run")).toContainText("Build login");

  // runs explorer table.
  await expect(page.getByTestId("runs-table")).toBeVisible();
  await expect(page.getByTestId(`run-row-${EXEC_ID}`)).toContainText("Build login");
  await expect(page.getByTestId(`run-row-${EXEC_ID}`)).toContainText("claude-opus");
  await expect(page.getByTestId(`run-row-${EXEC_ID}`)).toContainText("$0.050000");
});

// ---------------------------------------------------------------------------
// The window selector re-queries
// ---------------------------------------------------------------------------
test("the window selector re-queries with the chosen window", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN);
  await page.goto("/admin/tenant-stats", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("tenant-stats-dashboard")).toBeVisible();

  await page.getByTestId("window-365").click();
  await expect.poll(() => capture.windows).toContain(365);
});

// ---------------------------------------------------------------------------
// A runs filter re-queries
// ---------------------------------------------------------------------------
test("the verdict filter re-queries the runs explorer", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN);
  await page.goto("/admin/tenant-stats", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("runs-table")).toBeVisible();

  await page.getByTestId("filter-verdict").fill("aborted");
  await expect.poll(() => capture.verdicts).toContain("aborted");
});

// ---------------------------------------------------------------------------
// The currency note explains USD-canonical + display-only conversion
// ---------------------------------------------------------------------------
test("the dashboard shows the currency note", async ({ page }) => {
  await setup(page, TENANT_ADMIN);
  await page.goto("/admin/tenant-stats", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("currency-note")).toContainText("USD");
  await expect(page.getByTestId("currency-note")).toContainText("tasa de cambio");
});

// ---------------------------------------------------------------------------
// The currency toggle re-queries with display_currency and converts per row
// (Plan 11.1 task_11_1_03)
// ---------------------------------------------------------------------------
test("the currency toggle converts the runs explorer per run", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN);
  await page.goto("/admin/tenant-stats", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("runs-table")).toBeVisible();

  // Default USD: no converted column, no display_currency param.
  await expect(page.getByTestId("runs-col-converted")).toHaveCount(0);

  // Switch to EUR → re-query carries display_currency=EUR and a converted
  // column appears with the per-run-date amount.
  await page.getByTestId("currency-EUR").click();
  await expect.poll(() => capture.currencies).toContain("EUR");
  await expect(page.getByTestId("runs-col-converted")).toContainText("EUR");
  await expect(page.getByTestId(`run-converted-${EXEC_ID}`)).toContainText("0.05 EUR");
  // The canonical USD figure is still shown alongside.
  await expect(page.getByTestId(`run-row-${EXEC_ID}`)).toContainText("$0.050000");
});
