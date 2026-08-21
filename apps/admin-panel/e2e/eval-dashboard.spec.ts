import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the tenant eval QUALITY dashboard (Plan 14 task_14_11).
 *
 * A tenant_admin views how its agents score over time: pass rate by agent, by
 * prompt release and by dataset, the per-criterion breakdown, the daily trend
 * and the run history. The page is `<RoleGuard min="tenant_admin">`; the data
 * comes from two tenant-scoped (RLS) endpoints.
 *
 * Mocks:
 *   - GET /me                              — identity (admin vs plain user)
 *   - GET /eval-quality/dashboard          — aggregated quality
 *   - GET /eval-quality/runs               — run history
 *
 * Drives:
 *   - a plain user does NOT see the dashboard (RoleGuard fallback),
 *   - admin sees headline pass rate + per-agent / per-version / per-dataset /
 *     per-criterion breakdowns + the run-history table,
 *   - the window selector re-queries with the chosen window_days,
 *   - the USD-only currency note is shown (tenant-currency FX pending, Plan 11).
 *
 * NOTE: written, not run (task_14_11 e2e is pending human verification — there
 * is no browser in the implementing environment).
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const AGENT_ID = "a1111111-0000-0000-0000-000000000010";
const DATASET_ID = "d1111111-0000-0000-0000-000000000020";
const CRITERION_ID = "c1111111-0000-0000-0000-000000000030";
const RUN_ID = "f1111111-0000-0000-0000-000000000040";
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

function makeDashboard(windowDays: number) {
  return {
    window_days: windowDays,
    currency: "USD",
    total_runs: 3,
    total_items: 25,
    passed_items: 22,
    overall_pass_rate: "0.880",
    by_agent: [
      {
        subject_agent_id: AGENT_ID,
        agent_name: "Backend",
        agent_role: "backend",
        run_count: 2,
        total_items: 20,
        passed_items: 17,
        pass_rate: "0.850",
        mean_cost_usd: "0.030000",
        mean_tokens: "1500.00",
      },
    ],
    by_prompt_version: [
      {
        subject_prompt_version: "v2",
        run_count: 1,
        total_items: 10,
        passed_items: 9,
        pass_rate: "0.900",
        mean_cost_usd: "0.040000",
      },
    ],
    by_dataset: [
      {
        dataset_id: DATASET_ID,
        dataset_name: "Login golden",
        run_count: 2,
        total_items: 20,
        passed_items: 17,
        pass_rate: "0.850",
      },
    ],
    by_criterion: [
      {
        criterion_id: CRITERION_ID,
        criterion_name: "PEP 8",
        scored: 3,
        passed: 2,
        pass_rate: "0.667",
      },
    ],
    trend: [
      { day: "2026-05-29", run_count: 1, total_items: 10, passed_items: 8, pass_rate: "0.800" },
      { day: "2026-05-30", run_count: 2, total_items: 15, passed_items: 14, pass_rate: "0.933" },
    ],
  };
}

const RUNS = [
  {
    id: RUN_ID,
    dataset_id: DATASET_ID,
    dataset_name: "Login golden",
    status: "completed",
    subject_agent_id: AGENT_ID,
    agent_name: "Backend",
    agent_role: "backend",
    subject_prompt_version: "v2",
    judge_model: "claude-judge",
    started_at: "2026-05-30T10:00:00Z",
    finished_at: "2026-05-30T10:05:00Z",
    total_items: 10,
    passed_items: 9,
    pass_rate: "0.900",
    mean_latency_ms: "1200.00",
    mean_tokens: "2000.00",
    mean_cost_usd: "0.040000",
    created_at: "2026-05-30T10:00:00Z",
  },
];

interface Capture {
  windows: number[];
}

async function setup(
  page: Page,
  identity: typeof TENANT_ADMIN | typeof TENANT_USER = TENANT_ADMIN,
): Promise<Capture> {
  const capture: Capture = { windows: [] };

  await seedSession(page, { tenantId: TENANT_ID });

  await page.route(`${BASE}/me`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(identity) }),
  );

  await page.route(`${BASE}/eval-quality/dashboard**`, (route) => {
    const url = new URL(route.request().url());
    const windowDays = Number(url.searchParams.get("window_days") ?? "90");
    capture.windows.push(windowDays);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeDashboard(windowDays)),
    });
  });

  await page.route(`${BASE}/eval-quality/runs**`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(RUNS) }),
  );

  return capture;
}

// ---------------------------------------------------------------------------
// RBAC — a plain user does NOT see the dashboard
// ---------------------------------------------------------------------------
test("a plain user does not see the quality dashboard", async ({ page }) => {
  await setup(page, TENANT_USER);
  await page.goto("/admin/eval-quality", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("eval-quality-page")).toBeVisible();
  await expect(page.getByTestId("quality-dashboard")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Admin sees headline + breakdowns + run history
// ---------------------------------------------------------------------------
test("admin sees the aggregated quality breakdowns and run history", async ({ page }) => {
  await setup(page, TENANT_ADMIN);
  await page.goto("/admin/eval-quality", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("quality-dashboard")).toBeVisible();
  await expect(page.getByTestId("overall-pass-rate")).toContainText("88.0%");
  await expect(page.getByTestId("total-runs")).toContainText("3");

  await expect(page.getByTestId(`agent-row-${AGENT_ID}`)).toContainText("Backend");
  await expect(page.getByTestId(`agent-row-${AGENT_ID}`)).toContainText("85.0%");
  await expect(page.getByTestId("version-row-v2")).toContainText("90.0%");
  await expect(page.getByTestId(`dataset-row-${DATASET_ID}`)).toContainText("Login golden");
  await expect(page.getByTestId(`criterion-row-${CRITERION_ID}`)).toContainText("PEP 8");
  await expect(page.getByTestId(`criterion-row-${CRITERION_ID}`)).toContainText("66.7%");

  await expect(page.getByTestId("run-history-table")).toBeVisible();
  await expect(page.getByTestId(`run-row-${RUN_ID}`)).toContainText("Login golden");
  await expect(page.getByTestId(`run-row-${RUN_ID}`)).toContainText("$0.040000");
});

// ---------------------------------------------------------------------------
// The window selector re-queries
// ---------------------------------------------------------------------------
test("the window selector re-queries with the chosen window", async ({ page }) => {
  const capture = await setup(page, TENANT_ADMIN);
  await page.goto("/admin/eval-quality", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("quality-dashboard")).toBeVisible();

  await page.getByTestId("window-365").click();
  await expect.poll(() => capture.windows).toContain(365);
});

// ---------------------------------------------------------------------------
// The USD-only currency note is shown (tenant-currency FX pending, Plan 11)
// ---------------------------------------------------------------------------
test("the dashboard shows the USD-only currency note", async ({ page }) => {
  await setup(page, TENANT_ADMIN);
  await page.goto("/admin/eval-quality", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("currency-note")).toContainText("USD");
  await expect(page.getByTestId("currency-note")).toContainText("sistema FX");
});
