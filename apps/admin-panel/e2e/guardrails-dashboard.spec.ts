import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for /admin/guardrails — the tenant guardrails dashboard (Plan 11
 * task_11_20).
 *
 * The `guardrail_events` table is tenant-scoped (tenant_id + RLS): a tenant
 * sees ONLY its own events. Each event's detail is MASKED — the raw secret /
 * PII that tripped the guardrail is never persisted, so the dashboard never
 * shows a raw value. The screen is a tenant_admin surface
 * (`<RoleGuard min="tenant_admin">` + the backend gates with
 * `require_tenant_admin`): counts by type / severity, a daily trend
 * sparkline, and a recent-events table.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET /me                       — a TENANT ADMIN (dashboard visible)
 *   - GET /guardrails/dashboard**   — the aggregated dashboard payload
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_11_20 — it is
 * marked PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev
 * server). Run with `npx playwright test e2e/guardrails-dashboard.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const EVENT_ID = "eeee1111-0000-0000-0000-0000000000e1";

const TENANT_ADMIN = {
  user_id: "22222222-0000-0000-0000-000000000022",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    {
      tenant_id: TENANT_ID,
      tenant_name: "Tenant A",
      role: "tenant_admin",
      is_active: true,
    },
  ],
  active_tenant_id: TENANT_ID,
};

const TENANT_USER = {
  ...TENANT_ADMIN,
  user_id: "33333333-0000-0000-0000-000000000033",
  email: "user@tenant.test",
  full_name: "Tenant User",
  memberships: [
    {
      tenant_id: TENANT_ID,
      tenant_name: "Tenant A",
      role: "tenant_user",
      is_active: true,
    },
  ],
};

const DASHBOARD = {
  total: 5,
  window_days: 30,
  by_type: [
    { guardrail_type: "secret_leakage", count: 3 },
    { guardrail_type: "pii", count: 2 },
  ],
  by_severity: [
    { severity: "high", count: 3 },
    { severity: "medium", count: 2 },
  ],
  by_day: [
    { day: "2026-05-28", count: 1 },
    { day: "2026-05-29", count: 2 },
    { day: "2026-05-30", count: 2 },
  ],
  recent: [
    {
      id: EVENT_ID,
      tenant_id: TENANT_ID,
      guardrail_type: "secret_leakage",
      hook_point: "post_llm",
      severity: "high",
      action: "redact",
      project_id: null,
      agent_id: null,
      execution_id: null,
      agent_label: "planner",
      // MASKED detail — names the family, never the raw secret.
      detail: "Detected 1 leaked secret(s) [GITHUB_TOKEN] in post_llm text.",
      detail_payload: { secret_types: ["GITHUB_TOKEN"], count: 1 },
      created_at: "2026-05-30T10:00:00Z",
    },
  ],
};

async function setupAdmin(page: Page, me: unknown = TENANT_ADMIN): Promise<void> {
  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(me) }),
  );

  await page.route("http://localhost:8001/guardrails/dashboard**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DASHBOARD),
    }),
  );
}

// ---------------------------------------------------------------------------
// Tenant admin sees the full dashboard
// ---------------------------------------------------------------------------
test("renders counts, trend and recent events for a tenant admin", async ({ page }) => {
  await setupAdmin(page);
  await page.goto("/admin/guardrails", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("guardrails-page")).toBeVisible();
  await expect(page.getByTestId("guardrails-dashboard")).toBeVisible();

  // Total + trend sparkline.
  await expect(page.getByTestId("total-count")).toContainText("5");
  await expect(page.getByTestId("guardrails-sparkline")).toBeVisible();

  // Breakdown by type + severity.
  await expect(page.getByTestId("type-row-secret_leakage")).toContainText("3");
  await expect(page.getByTestId("type-row-pii")).toContainText("2");
  await expect(page.getByTestId("severity-row-high")).toBeVisible();
  await expect(page.getByTestId("severity-row-medium")).toBeVisible();
});

test("recent events show the MASKED detail, never a raw secret", async ({ page }) => {
  await setupAdmin(page);
  await page.goto("/admin/guardrails", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("recent-events-table")).toBeVisible();
  const row = page.getByTestId(`event-row-${EVENT_ID}`);
  await expect(row).toBeVisible();
  await expect(row).toContainText("secret_leakage");
  await expect(row).toContainText("GITHUB_TOKEN");
  // The masked summary is shown — no ghp_ token shape ever appears.
  await expect(row).not.toContainText("ghp_");
});

// ---------------------------------------------------------------------------
// Window selector re-queries with the chosen window
// ---------------------------------------------------------------------------
test("switching the window re-requests the dashboard with window_days", async ({ page }) => {
  await setupAdmin(page);

  let lastUrl = "";
  await page.route("http://localhost:8001/guardrails/dashboard**", (route) => {
    lastUrl = route.request().url();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DASHBOARD),
    });
  });

  await page.goto("/admin/guardrails", { waitUntil: "domcontentloaded" });
  await page.getByTestId("window-7").click();
  await expect.poll(() => lastUrl).toContain("window_days=7");
});

// ---------------------------------------------------------------------------
// RoleGuard: a plain tenant_user does not see the dashboard body
// ---------------------------------------------------------------------------
test("a plain tenant_user sees the role-gated fallback, not the dashboard", async ({ page }) => {
  await setupAdmin(page, TENANT_USER);
  await page.goto("/admin/guardrails", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("guardrails-page")).toBeVisible();
  await expect(page.getByTestId("guardrails-dashboard")).toHaveCount(0);
});
