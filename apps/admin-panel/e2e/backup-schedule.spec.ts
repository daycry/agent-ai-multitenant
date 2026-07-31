import { expect, test, type Page, type Route } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/backup — the System-Admin 'Programación de backups' screen
 * (Plan 12 task_12_04).
 *
 * The daily backup schedule (cron cadence + a live enable flag + the local
 * retention window) is a PLATFORM setting a System Admin configures here —
 * NOT a hardcoded cron. The three values live in `platform_settings`
 * (`backup_enabled` / `backup_cron` / `backup_retention_days`); the backup
 * beat task reads them live so a change takes effect on the next run.
 *
 * Read/write split (mirrors the notifications platform-settings pattern):
 *   - READ open to any authenticated member:  GET /admin/backup/schedule
 *   - WRITE System-Admin only:                PUT /admin/backup/schedule
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET /me                       — a SYSTEM ADMIN (the form is editable)
 *   - GET /admin/backup/schedule    — current schedule
 *   - PUT /admin/backup/schedule    — persists (200) or rejects (422)
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_12_04 — it is marked
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/backup-schedule.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const SCHEDULE_URL = "http://localhost:8001/admin/backup/schedule";

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

const TENANT_USER = {
  ...SYSTEM_ADMIN,
  user_id: "88888888-0000-0000-0000-000000000088",
  email: "user@platform.test",
  full_name: "Plain User",
  is_system_admin: false,
  memberships: [
    {
      tenant_id: TENANT_ID,
      tenant_name: "Tenant A",
      role: "tenant_user",
      is_active: true,
    },
  ],
};

const DEFAULT_SCHEDULE = { enabled: true, cron: "0 3 * * *", retention_days: 7 };

/** Seed a fake token + tenant and route /me to the given identity. */
async function setup(
  page: Page,
  identity: typeof SYSTEM_ADMIN | typeof TENANT_USER = SYSTEM_ADMIN,
  schedule: typeof DEFAULT_SCHEDULE = DEFAULT_SCHEDULE,
): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(identity),
    }),
  );

  await page.route(SCHEDULE_URL, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(schedule),
      });
    }
    return route.fallback();
  });
}

// ---------------------------------------------------------------------------
// Read — the form is seeded from the current schedule.
// ---------------------------------------------------------------------------
test("system admin sees the current schedule seeded into the form", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/backup", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("backup-schedule-page")).toBeVisible();
  await expect(page.getByTestId("backup-schedule-form")).toBeVisible();
  await expect(page.getByTestId("backup-enabled-input")).toBeChecked();
  await expect(page.getByTestId("backup-cron-input")).toHaveValue("0 3 * * *");
  await expect(page.getByTestId("backup-retention-input")).toHaveValue("7");
});

// ---------------------------------------------------------------------------
// Write — System Admin persists cron + window + retention.
// ---------------------------------------------------------------------------
test("system admin updates the cron, window and retention", async ({ page }) => {
  await setup(page);

  let put: Record<string, unknown> = {};
  await page.route(SCHEDULE_URL, (route: Route) => {
    if (route.request().method() === "PUT") {
      put = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(put),
      });
    }
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(DEFAULT_SCHEDULE),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/backup", { waitUntil: "domcontentloaded" });

  await page.getByTestId("backup-cron-input").fill("30 2 * * *");
  await page.getByTestId("backup-retention-input").fill("14");
  await page.getByTestId("backup-enabled-input").uncheck();
  await page.getByTestId("backup-schedule-submit").click();

  await expect.poll(() => put.cron).toBe("30 2 * * *");
  expect(put.retention_days).toBe(14);
  expect(put.enabled).toBe(false);
  await expect(page.getByTestId("backup-schedule-saved")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Validation — a 422 from the backend surfaces the error.
// ---------------------------------------------------------------------------
test("a rejected cron (422) shows the backend error", async ({ page }) => {
  await setup(page);

  await page.route(SCHEDULE_URL, (route: Route) => {
    if (route.request().method() === "PUT") {
      return route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "invalid cron expression: '99 3 * * *'" }),
      });
    }
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(DEFAULT_SCHEDULE),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/backup", { waitUntil: "domcontentloaded" });
  await page.getByTestId("backup-cron-input").fill("99 3 * * *");
  await page.getByTestId("backup-schedule-submit").click();

  await expect(page.getByTestId("backup-schedule-save-error")).toContainText("invalid cron");
});

// ---------------------------------------------------------------------------
// Client-side guard — an out-of-range retention disables the submit.
// ---------------------------------------------------------------------------
test("an out-of-range retention disables the submit button", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/backup", { waitUntil: "domcontentloaded" });

  // 0 days is below the floor (1) — the form blocks the submit before any PUT.
  await page.getByTestId("backup-retention-input").fill("0");
  await expect(page.getByTestId("backup-schedule-submit")).toBeDisabled();
});

// ---------------------------------------------------------------------------
// RBAC — a non-System-Admin gets a read-only view (no editable form).
// ---------------------------------------------------------------------------
test("a plain tenant user sees a read-only schedule, not the form", async ({ page }) => {
  await setup(page, TENANT_USER);
  await page.goto("/admin/backup", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("backup-schedule-readonly")).toBeVisible();
  await expect(page.getByTestId("backup-schedule-form")).toHaveCount(0);
  await expect(page.getByTestId("backup-readonly-cron")).toContainText("0 3 * * *");
});
