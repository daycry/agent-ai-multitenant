import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for the price-sync DIFF + mandatory confirmation flow (Plan 11 task_11_16).
 *
 * The "Sincronizar precios" button on /admin/model-prices drives a two-step
 * flow against the LiteLLM community price JSON (a DATA FEED only — ADR 0021,
 * NOT a provider runtime):
 *
 *   1) DRY-RUN  POST /admin/model-prices/sync/diff  computes a per-model diff
 *      (added / updated / unchanged / increased / removed, old-vs-new prices +
 *      % change) WITHOUT writing the catalog.
 *   2) APPLY    POST /admin/model-prices/sync/apply  writes the catalog with
 *      effective dating. If ANY price rises >10% the backend REJECTS the apply
 *      (409) unless the UI passes `confirm: true`; the dialog gates an explicit
 *      confirmation checkbox on `has_large_increase` so a human reviews the
 *      spike before applying.
 *
 * System-Admin only (writes); the backend gates on require_system_admin.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET    /me                              — a SYSTEM ADMIN (writes visible)
 *   - GET    /model-prices**                  — catalog list (header/list)
 *   - POST   /admin/model-prices/sync/diff    — the dry-run diff
 *   - POST   /admin/model-prices/sync/apply   — the apply (echoes confirm)
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_11_16 — it is marked
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/prices-diff.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";

const SYSTEM_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "sysadmin@platform.test",
  full_name: "System Admin",
  is_system_admin: true,
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

// A diff with one within-threshold update, one >10% spike, one added, one
// removed — exercises both the table rendering and the confirmation gate.
const DIFF_WITH_SPIKE = {
  fetched: 3,
  added: 1,
  updated: 1,
  unchanged: 4,
  increased: 1,
  removed: 1,
  has_large_increase: true,
  rows: [
    {
      provider: "anthropic",
      model_id: "claude-sonnet-4-5",
      modality: "text",
      status: "updated",
      source: "litellm",
      old_input: "3.0000000000",
      new_input: "3.0000000000",
      old_output: "15.0000000000",
      new_output: "16.0000000000",
      old_cached_input: "0.3000000000",
      new_cached_input: "0.3000000000",
      input_pct: 0,
      output_pct: 0.0667,
      manual_skipped: false,
    },
    {
      provider: "openai",
      model_id: "text-embedding-3-small",
      modality: "embedding",
      status: "increased",
      source: "litellm",
      old_input: "0.0200000000",
      new_input: "0.0500000000",
      old_output: "0E-10",
      new_output: "0E-10",
      old_cached_input: null,
      new_cached_input: null,
      input_pct: 1.5,
      output_pct: null,
      manual_skipped: false,
    },
    {
      provider: "openai",
      model_id: "gpt-new",
      modality: "text",
      status: "added",
      source: "litellm",
      old_input: null,
      new_input: "1.0000000000",
      old_output: null,
      new_output: "2.0000000000",
      old_cached_input: null,
      new_cached_input: null,
      input_pct: null,
      output_pct: null,
      manual_skipped: false,
    },
    {
      provider: "anthropic",
      model_id: "claude-legacy",
      modality: "text",
      status: "removed",
      source: "manual",
      old_input: "1.0000000000",
      new_input: null,
      old_output: "2.0000000000",
      new_output: null,
      old_cached_input: null,
      new_cached_input: null,
      input_pct: null,
      output_pct: null,
      manual_skipped: false,
    },
  ],
  skipped: [],
};

// A diff with no >10% rise — apply proceeds without confirmation.
const DIFF_NO_SPIKE = {
  ...DIFF_WITH_SPIKE,
  increased: 0,
  has_large_increase: true && false, // explicit: no spike
  rows: DIFF_WITH_SPIKE.rows.filter((r) => r.status !== "increased"),
};

async function setup(page: Page): Promise<void> {
  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(SYSTEM_ADMIN),
    }),
  );

  // The list/header reads — return an empty catalog (irrelevant to the sync).
  await page.route("http://localhost:8001/model-prices**", (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });
}

// ---------------------------------------------------------------------------
// The diff view renders the per-model changes (old vs new + % change).
// ---------------------------------------------------------------------------
test("dry-run diff shows per-model changes with % change", async ({ page }) => {
  await setup(page);
  await page.route("http://localhost:8001/admin/model-prices/sync/diff", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DIFF_WITH_SPIKE),
    }),
  );

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId("price-sync-open").click();
  await expect(page.getByTestId("price-sync-dialog")).toBeVisible();
  await expect(page.getByTestId("sync-diff-table")).toBeVisible();

  // The within-threshold update + its % change.
  await expect(page.getByTestId("sync-row-claude-sonnet-4-5")).toBeVisible();
  await expect(page.getByTestId("sync-output-pct-claude-sonnet-4-5")).toContainText("+6.7%");
  // The >10% spike row + its % change.
  await expect(page.getByTestId("sync-row-text-embedding-3-small")).toHaveAttribute(
    "data-status",
    "increased",
  );
  await expect(page.getByTestId("sync-input-pct-text-embedding-3-small")).toContainText("+150%");
});

// ---------------------------------------------------------------------------
// A >10% rise gates the apply behind an explicit confirmation checkbox, and
// the apply request carries confirm=true once ticked.
// ---------------------------------------------------------------------------
test("a >10% rise requires explicit confirmation before applying", async ({ page }) => {
  await setup(page);
  await page.route("http://localhost:8001/admin/model-prices/sync/diff", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DIFF_WITH_SPIKE),
    }),
  );

  let appliedBody: Record<string, unknown> = {};
  await page.route("http://localhost:8001/admin/model-prices/sync/apply", (route: Route) => {
    appliedBody = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ fetched: 3, created: 1, updated: 2, unchanged: 4, changed: 3 }),
    });
  });

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId("price-sync-open").click();

  // The confirmation gate is shown and the apply button starts disabled.
  await expect(page.getByTestId("sync-confirm-gate")).toBeVisible();
  await expect(page.getByTestId("sync-apply")).toBeDisabled();

  // Tick the explicit confirmation -> apply enabled.
  await page.getByTestId("sync-confirm-checkbox").check();
  await expect(page.getByTestId("sync-apply")).toBeEnabled();
  await page.getByTestId("sync-apply").click();

  // The apply request carried confirm=true.
  await expect.poll(() => appliedBody.confirm).toBe(true);
});

// ---------------------------------------------------------------------------
// A backend 409 (apply rejected because confirm wasn't honoured) surfaces.
// ---------------------------------------------------------------------------
test("apply without confirm is rejected by the backend (409 surfaced)", async ({ page }) => {
  await setup(page);
  await page.route("http://localhost:8001/admin/model-prices/sync/diff", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DIFF_WITH_SPIKE),
    }),
  );
  await page.route("http://localhost:8001/admin/model-prices/sync/apply", (route: Route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          message: "1 model price(s) rose more than 10%; explicit confirmation required",
          large_increases: [
            { provider: "openai", model_id: "text-embedding-3-small", field: "input_price" },
          ],
        },
      }),
    }),
  );

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId("price-sync-open").click();
  await page.getByTestId("sync-confirm-checkbox").check();
  await page.getByTestId("sync-apply").click();

  await expect(page.getByTestId("sync-apply-error")).toBeVisible();
});

// ---------------------------------------------------------------------------
// A <=10% change applies without any confirmation gate.
// ---------------------------------------------------------------------------
test("a <=10% change applies without a confirmation gate", async ({ page }) => {
  await setup(page);
  await page.route("http://localhost:8001/admin/model-prices/sync/diff", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DIFF_NO_SPIKE),
    }),
  );

  let appliedBody: Record<string, unknown> = {};
  await page.route("http://localhost:8001/admin/model-prices/sync/apply", (route: Route) => {
    appliedBody = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ fetched: 3, created: 1, updated: 1, unchanged: 4, changed: 2 }),
    });
  });

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId("price-sync-open").click();
  await expect(page.getByTestId("sync-diff-table")).toBeVisible();

  // No confirmation gate; apply is enabled straight away.
  await expect(page.getByTestId("sync-confirm-gate")).toHaveCount(0);
  await expect(page.getByTestId("sync-apply")).toBeEnabled();
  await page.getByTestId("sync-apply").click();

  // The apply request did NOT request a confirmation override.
  await expect.poll(() => appliedBody.confirm).toBe(false);
});
