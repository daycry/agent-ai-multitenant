import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for /admin/model-prices — the System-Admin 'Modelos & Precios'
 * screen (Plan 11 task_11_14).
 *
 * The global price catalog (Plan 11 Fase C) is **platform-global** (no
 * tenant_id), **USD-canonical** and supports **prompt caching**
 * (cached_input_price). This screen lets a System Admin list the catalog
 * with filters (provider / model_id / modality + current-only), create /
 * edit / supersede prices (System-Admin only), and view per-model price
 * history (effective-dated rows) + a price-over-time chart.
 *
 * Read/write split (mirrors the marketplace global-catalog split):
 *   - READS open to any authenticated caller (global-read RLS, migration
 *     0049):  GET /model-prices[/current]
 *   - WRITES System-Admin only:  POST/PATCH/DELETE /admin/model-prices
 *
 * USD-canonical: no currency knob — the catalog is USD-only; the form
 * states the prices are in canonical USD.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET    /me                         — a SYSTEM ADMIN (writes visible)
 *   - GET    /model-prices**             — catalog list / history
 *   - POST   /admin/model-prices         — create (201)
 *   - PATCH  /admin/model-prices/{id}    — edit (200)
 *   - DELETE /admin/model-prices/{id}    — supersede (200)
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_11_14 — it is
 * marked PENDING HUMAN VERIFICATION (needs a browser + the admin-panel
 * dev server). Run with `npx playwright test e2e/admin-models-prices.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const CURRENT_ID = "aaaa1111-0000-0000-0000-0000000000a1";
const OLD_ID = "aaaa2222-0000-0000-0000-0000000000a2";
const NEW_ID = "aaaa3333-0000-0000-0000-0000000000a3";

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

const CURRENT_PRICE = {
  id: CURRENT_ID,
  provider: "anthropic",
  model_id: "claude-sonnet-4-5",
  modality: "text",
  input_price: "3.0000000000",
  output_price: "15.0000000000",
  cached_input_price: "0.3000000000",
  unit: "per_1m_tokens",
  currency: "USD",
  context_window: 200000,
  source: "manual",
  effective_from: "2026-05-01T00:00:00Z",
  effective_to: null,
  updated_by: SYSTEM_ADMIN.user_id,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
};

const OLD_PRICE = {
  ...CURRENT_PRICE,
  id: OLD_ID,
  input_price: "2.5000000000",
  output_price: "12.0000000000",
  cached_input_price: null,
  effective_from: "2026-01-01T00:00:00Z",
  effective_to: "2026-05-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-05-01T00:00:00Z",
};

/** Route both /me and the read catalog. Returns nothing; per-test write routes added by callers. */
async function setup(page: Page, listRows: unknown[] = [CURRENT_PRICE]): Promise<void> {
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

  await page.route("http://localhost:8001/model-prices**", (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(listRows),
    });
  });
}

// ---------------------------------------------------------------------------
// List + filters
// ---------------------------------------------------------------------------
test("lists the catalog with USD prices and a current badge", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("model-prices-page")).toBeVisible();
  await expect(page.getByTestId("prices-table")).toBeVisible();
  await expect(page.getByTestId(`price-row-${CURRENT_ID}`)).toBeVisible();
  // USD-canonical formatting + the cached (prompt-caching) price.
  await expect(page.getByTestId(`price-input-${CURRENT_ID}`)).toContainText("$3");
  await expect(page.getByTestId(`price-cached-${CURRENT_ID}`)).toContainText("$0.3");
  await expect(page.getByTestId(`price-current-${CURRENT_ID}`)).toBeVisible();
});

test("applies provider/model/modality filters to the list request", async ({ page }) => {
  await setup(page);

  let lastUrl = "";
  await page.route("http://localhost:8001/model-prices**", (route) => {
    if (route.request().method() === "GET") {
      lastUrl = route.request().url();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([CURRENT_PRICE]),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId("filter-provider").fill("anthropic");
  await page.getByTestId("filter-model").fill("claude-sonnet-4-5");
  await page.getByTestId("filter-modality").selectOption("text");
  await page.getByTestId("filter-apply").click();

  await expect.poll(() => lastUrl).toContain("provider=anthropic");
  expect(lastUrl).toContain("model_id=claude-sonnet-4-5");
  expect(lastUrl).toContain("modality=text");
});

// ---------------------------------------------------------------------------
// Create (System Admin) — USD-canonical, no currency on the wire
// ---------------------------------------------------------------------------
test("system admin creates a price (USD-canonical, no currency field)", async ({ page }) => {
  await setup(page);

  let posted: Record<string, unknown> = {};
  await page.route("http://localhost:8001/admin/model-prices", (route: Route) => {
    if (route.request().method() === "POST") {
      posted = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ ...CURRENT_PRICE, id: NEW_ID }),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId("price-create-open").click();
  await expect(page.getByTestId("price-form-dialog")).toBeVisible();

  await page.getByTestId("form-provider").fill("openai");
  await page.getByTestId("form-model").fill("gpt-5");
  await page.getByTestId("form-input-price").fill("5");
  await page.getByTestId("form-output-price").fill("20");
  await page.getByTestId("form-cached-price").fill("0.5");
  await page.getByTestId("price-form-submit").click();

  await expect.poll(() => posted.provider).toBe("openai");
  expect(posted.model_id).toBe("gpt-5");
  expect(posted.input_price).toBe("5");
  expect(posted.cached_input_price).toBe("0.5");
  // USD-canonical: the form never sends a currency knob.
  expect(posted).not.toHaveProperty("currency");
});

// ---------------------------------------------------------------------------
// Edit (System Admin) — key is immutable, only mutable fields PATCHed
// ---------------------------------------------------------------------------
test("system admin edits a price; the catalog key is immutable", async ({ page }) => {
  await setup(page);

  let patched: Record<string, unknown> = {};
  await page.route(`http://localhost:8001/admin/model-prices/${CURRENT_ID}`, (route: Route) => {
    if (route.request().method() === "PATCH") {
      patched = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...CURRENT_PRICE, output_price: "18.0000000000" }),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`price-edit-${CURRENT_ID}`).click();
  await expect(page.getByTestId("price-form-dialog")).toBeVisible();

  // The key fields are disabled (immutable) on edit.
  await expect(page.getByTestId("form-provider")).toBeDisabled();
  await expect(page.getByTestId("form-model")).toBeDisabled();
  await expect(page.getByTestId("form-modality")).toBeDisabled();

  await page.getByTestId("form-output-price").fill("18");
  await page.getByTestId("price-form-submit").click();

  await expect.poll(() => patched.output_price).toBe("18");
  expect(patched).not.toHaveProperty("provider");
});

// ---------------------------------------------------------------------------
// Supersede (System Admin) — close the period, not a hard delete
// ---------------------------------------------------------------------------
test("system admin supersedes the current price", async ({ page }) => {
  await setup(page);

  let deletedPath: string | null = null;
  await page.route(`http://localhost:8001/admin/model-prices/${CURRENT_ID}`, (route: Route) => {
    if (route.request().method() === "DELETE") {
      deletedPath = new URL(route.request().url()).pathname;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...CURRENT_PRICE, effective_to: "2026-06-01T00:00:00Z" }),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`price-supersede-${CURRENT_ID}`).click();

  await expect.poll(() => deletedPath).toBe(`/admin/model-prices/${CURRENT_ID}`);
});

// ---------------------------------------------------------------------------
// History — effective-dated rows + price-over-time chart
// ---------------------------------------------------------------------------
test("opens per-model price history with effective-dated rows and a chart", async ({ page }) => {
  // The history dialog re-queries the catalog WITHOUT current_only, so it
  // returns both the open period and the closed (historical) one.
  await setup(page, [CURRENT_PRICE, OLD_PRICE]);

  await page.goto("/admin/model-prices", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`price-history-${CURRENT_ID}`).click();

  await expect(page.getByTestId("price-history-dialog")).toBeVisible();
  await expect(page.getByTestId("history-table")).toBeVisible();
  await expect(page.getByTestId(`history-row-${CURRENT_ID}`)).toBeVisible();
  await expect(page.getByTestId(`history-row-${OLD_ID}`)).toBeVisible();
  // Price-over-time chart (pure SVG, no heavy chart dep).
  await expect(page.getByTestId("price-chart")).toBeVisible();
});
