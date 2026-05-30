import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for /admin/backup/destinations — the System-Admin 'Destinos remotos de
 * backup' screen (Plan 12 Phase B task_12_09).
 *
 * After a successful, verified backup the bundle is uploaded to each configured
 * + enabled remote destination (S3, B2, SFTP/NAS, rclone). A System Admin
 * manages the list here and probes each destination's connectivity.
 *
 * Secrets are NEVER configured nor displayed here: the UI handles only the
 * NON-secret config (bucket, endpoint, host, path, remote). The CREDENTIALS live
 * in the workers' secret seam (Vault/env); the backend rejects (422) any
 * secret-looking field, so a credential can never be stored nor read back.
 *
 * Read/write split (mirrors the backup-schedule pattern):
 *   - READ open to any authenticated member:  GET  /admin/backup/destinations
 *   - WRITE + TEST System-Admin only:          PUT  /admin/backup/destinations
 *                                              POST /admin/backup/destinations/{name}/test
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET  /me                                       — a SYSTEM ADMIN
 *   - GET  /admin/backup/destinations                — current list
 *   - PUT  /admin/backup/destinations                — persists (echoes payload)
 *   - POST /admin/backup/destinations/{name}/test    — connectivity result
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_12_09 — it is marked
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/backup-destinations.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const LIST_URL = "http://localhost:8001/admin/backup/destinations";

const SYSTEM_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "sysadmin@platform.test",
  full_name: "System Admin",
  is_system_admin: true,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
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
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_user", is_active: true },
  ],
};

const S3_DEST = {
  type: "s3",
  name: "offsite-s3",
  enabled: true,
  config: { bucket: "backups", prefix: "nightly/", endpoint_url: "https://minio:9000" },
};

/** Seed a fake token + tenant and route /me to the given identity. */
async function setup(
  page: Page,
  identity: typeof SYSTEM_ADMIN | typeof TENANT_USER = SYSTEM_ADMIN,
  destinations: Array<typeof S3_DEST> = [],
): Promise<void> {
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
      body: JSON.stringify(identity),
    }),
  );

  await page.route(LIST_URL, (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ destinations }),
      });
    }
    return route.fallback();
  });
}

// ---------------------------------------------------------------------------
// Read — the list is rendered into the editor; an empty list is handled.
// ---------------------------------------------------------------------------
test("system admin sees the empty editor when no destinations exist", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/backup/destinations", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("backup-destinations-page")).toBeVisible();
  await expect(page.getByTestId("backup-destinations-editor")).toBeVisible();
  await expect(page.getByTestId("backup-destinations-empty")).toBeVisible();
});

test("system admin sees an existing destination seeded into the editor", async ({ page }) => {
  await setup(page, SYSTEM_ADMIN, [S3_DEST]);
  await page.goto("/admin/backup/destinations", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("backup-destination-name-0")).toHaveValue("offsite-s3");
  await expect(page.getByTestId("backup-destination-type-0")).toHaveValue("s3");
  await expect(page.getByTestId("backup-destination-0-bucket")).toHaveValue("backups");
});

// ---------------------------------------------------------------------------
// Write — System Admin adds a destination and persists the full list.
// ---------------------------------------------------------------------------
test("system admin adds a destination and saves the list", async ({ page }) => {
  await setup(page);

  let put: Record<string, unknown> = {};
  await page.route(LIST_URL, (route: Route) => {
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
        body: JSON.stringify({ destinations: [] }),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/backup/destinations", { waitUntil: "domcontentloaded" });

  await page.getByTestId("backup-destination-add").click();
  await page.getByTestId("backup-destination-name-0").fill("offsite-s3");
  await page.getByTestId("backup-destination-0-bucket").fill("backups");
  await page.getByTestId("backup-destinations-submit").click();

  await expect
    .poll(() => (put.destinations as Array<{ name: string }>)?.[0]?.name)
    .toBe("offsite-s3");
  const sent = (put.destinations as Array<{ type: string; config: Record<string, string> }>)[0];
  expect(sent.type).toBe("s3");
  expect(sent.config.bucket).toBe("backups");
  await expect(page.getByTestId("backup-destinations-saved")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Test connection — the button surfaces the backend connectivity result.
// ---------------------------------------------------------------------------
test("the test-connection button surfaces an OK result", async ({ page }) => {
  await setup(page, SYSTEM_ADMIN, [S3_DEST]);

  await page.route(
    "http://localhost:8001/admin/backup/destinations/offsite-s3/test",
    (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, detail: "bucket 'backups' reachable" }),
      }),
  );

  await page.goto("/admin/backup/destinations", { waitUntil: "domcontentloaded" });
  await page.getByTestId("backup-destination-test-0").click();

  await expect(page.getByTestId("backup-destination-test-result-0")).toContainText("OK");
  await expect(page.getByTestId("backup-destination-test-result-0")).toContainText("reachable");
});

test("the test-connection button surfaces an error result", async ({ page }) => {
  await setup(page, SYSTEM_ADMIN, [S3_DEST]);

  await page.route(
    "http://localhost:8001/admin/backup/destinations/offsite-s3/test",
    (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: false,
          detail: "head_bucket on 'backups' failed: AccessDenied",
        }),
      }),
  );

  await page.goto("/admin/backup/destinations", { waitUntil: "domcontentloaded" });
  await page.getByTestId("backup-destination-test-0").click();

  await expect(page.getByTestId("backup-destination-test-result-0")).toContainText("Error");
  await expect(page.getByTestId("backup-destination-test-result-0")).toContainText("AccessDenied");
});

// ---------------------------------------------------------------------------
// RBAC — a non-System-Admin gets a read-only view (no editor, no test button).
// ---------------------------------------------------------------------------
test("a plain tenant user sees a read-only list, not the editor", async ({ page }) => {
  await setup(page, TENANT_USER, [S3_DEST]);
  await page.goto("/admin/backup/destinations", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("backup-destinations-readonly")).toBeVisible();
  await expect(page.getByTestId("backup-destinations-editor")).toHaveCount(0);
  await expect(page.getByTestId("backup-destination-test-0")).toHaveCount(0);
});
