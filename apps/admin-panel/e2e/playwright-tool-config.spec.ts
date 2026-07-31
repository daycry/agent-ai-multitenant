import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/marketplace/listings/{id}/playwright-config — the guided
 * configuration form for the flagship Playwright marketplace tool
 * (Plan 09 task_09_13).
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET /me                            — tenant_admin membership
 *   - GET /marketplace/listings/{id}     — the verified GLOBAL Playwright
 *                                          listing whose manifest carries the
 *                                          guided `config_schema`
 *
 * Drives:
 *   - the page renders the guided form (browsers / headless / screenshots /
 *     traces / base_url / timeout) from the listing's config_schema,
 *   - chromium is selected by default; toggling browsers updates the
 *     resulting config,
 *   - choosing a screenshot + trace mode updates the resulting config,
 *   - a non-positive timeout shows a validation error (mirrors the typed
 *     PlaywrightToolConfig.from_dict rejection),
 *   - the resulting config object reflects every choice.
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_09_13 — it is marked
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server).
 * Run it with `npx playwright test e2e/playwright-tool-config.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const LISTING_ID = "55555555-0000-0000-0000-000000000005";

const ME = {
  user_id: "44444444-0000-0000-0000-000000000004",
  email: "owner@playwright.test",
  full_name: "Project Owner",
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

// The verified GLOBAL (tenant_id NULL) Playwright listing — its manifest
// carries the guided config_schema the page renders.
const LISTING = {
  id: LISTING_ID,
  source_id: "66666666-0000-0000-0000-000000000006",
  tenant_id: null,
  kind: "tool",
  name: "playwright",
  version: "1.0.0",
  description: "End-to-end browser automation and testing with Microsoft Playwright.",
  author: "Platform",
  trust_level: "verified",
  requested_permissions: [
    { type: "allowed_domains", value: ["localhost"] },
    { type: "network_policy", value: "restricted" },
  ],
  is_signed: true,
  manifest: {
    name: "playwright",
    version: "1.0.0",
    kind: "tool",
    config_schema: {
      type: "object",
      properties: {
        browsers: {
          type: "array",
          widget: "multiselect",
          items: { enum: ["chromium", "firefox", "webkit"] },
          minItems: 1,
          default: ["chromium"],
        },
        headless: { type: "boolean", widget: "toggle", default: true },
        screenshots: {
          type: "string",
          widget: "select",
          enum: ["off", "on", "only-on-failure"],
          default: "only-on-failure",
        },
        traces: {
          type: "string",
          widget: "select",
          enum: ["off", "on", "retain-on-failure"],
          default: "retain-on-failure",
        },
        base_url: { type: "string", widget: "text", default: null },
        timeout_ms: { type: "integer", widget: "number", minimum: 1, default: 30000 },
      },
      required: ["browsers"],
    },
  },
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

async function setup(page: Page): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ME) }),
  );

  await page.route(`http://localhost:8001/marketplace/listings/${LISTING_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(LISTING),
    }),
  );
}

function parsePreview(text: string | null): Record<string, unknown> {
  return JSON.parse(text ?? "{}") as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Initial render — guided form with chromium default
// ---------------------------------------------------------------------------
test("renders the guided config form with chromium selected by default", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/marketplace/listings/${LISTING_ID}/playwright-config`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("playwright-config-page")).toBeVisible();
  await expect(page.getByTestId("playwright-listing-version")).toContainText("playwright 1.0.0");
  await expect(page.getByTestId("playwright-field-browsers")).toBeVisible();
  await expect(page.getByTestId("playwright-browser-chromium")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByTestId("playwright-browser-firefox")).toHaveAttribute(
    "aria-pressed",
    "false",
  );

  const preview = parsePreview(await page.getByTestId("playwright-config-preview").textContent());
  expect(preview.browsers).toEqual(["chromium"]);
  expect(preview.headless).toBe(true);
  expect(preview.screenshots).toBe("only-on-failure");
  expect(preview.traces).toBe("retain-on-failure");
  expect(preview.timeout_ms).toBe(30000);
});

// ---------------------------------------------------------------------------
// Multi-select browsers
// ---------------------------------------------------------------------------
test("toggling browsers updates the resulting config (multi-select)", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/marketplace/listings/${LISTING_ID}/playwright-config`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("playwright-browser-webkit").click();
  await expect(page.getByTestId("playwright-browser-webkit")).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  const preview = parsePreview(await page.getByTestId("playwright-config-preview").textContent());
  expect(preview.browsers).toEqual(["chromium", "webkit"]);
});

// ---------------------------------------------------------------------------
// Screenshots + traces selects
// ---------------------------------------------------------------------------
test("choosing screenshot and trace modes updates the config", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/marketplace/listings/${LISTING_ID}/playwright-config`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("playwright-screenshots-select").selectOption("on");
  await page.getByTestId("playwright-traces-select").selectOption("off");

  const preview = parsePreview(await page.getByTestId("playwright-config-preview").textContent());
  expect(preview.screenshots).toBe("on");
  expect(preview.traces).toBe("off");
});

// ---------------------------------------------------------------------------
// Headless toggle + base_url
// ---------------------------------------------------------------------------
test("toggling headless and setting base_url updates the config", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/marketplace/listings/${LISTING_ID}/playwright-config`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("playwright-headless-toggle").click();
  await page.getByTestId("playwright-base-url").fill("https://staging.example.test");

  const preview = parsePreview(await page.getByTestId("playwright-config-preview").textContent());
  expect(preview.headless).toBe(false);
  expect(preview.base_url).toBe("https://staging.example.test");
});

// ---------------------------------------------------------------------------
// Validation — non-positive timeout
// ---------------------------------------------------------------------------
test("a non-positive timeout shows a validation error", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/marketplace/listings/${LISTING_ID}/playwright-config`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("playwright-timeout").fill("0");
  await expect(page.getByTestId("playwright-config-validation")).toContainText("timeout");
});
