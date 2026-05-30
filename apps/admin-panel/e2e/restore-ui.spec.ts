import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for /admin/backup/restore — the System-Admin restore screen (Plan 12
 * Phase C task_12_12).
 *
 * A restore reconstructs the stack (or a single tenant) from a backup bundle. It
 * is LONG + DESTRUCTIVE, so the trigger ENQUEUES a Celery background job (it is
 * never run inline) and the UI polls the job's status. A DOUBLE confirmation is
 * required: the operator must type the exact confirm token (the bundle id for a
 * full restore; `<tenant_id>@<backup_id>` for a per-tenant one); the backend
 * re-derives + checks it server-side.
 *
 * The screen flow exercised here:
 *   - list backups (local + remote)        GET  /admin/backup/restore/backups
 *   - preview a bundle's manifest           GET  /admin/backup/restore/backups/{id}/preview
 *   - full vs per-tenant choice
 *   - double-confirmation dialog
 *   - trigger -> job id                     POST /admin/backup/restore
 *   - progress/log poll                     GET  /admin/backup/restore/jobs/{job_id}
 *
 * RBAC: System-Admin only — a plain tenant user sees the forbidden notice.
 *
 * Mocks the backend so the test runs fully offline. NOTE: this spec is WRITTEN
 * but NOT run as part of task_12_12 — it is PENDING HUMAN VERIFICATION (needs a
 * browser + the admin-panel dev server). Run with
 * `npx playwright test e2e/restore-ui.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const BACKUP_ID = "20260530T031500Z";
const API = "http://localhost:8001";

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

const BACKUP_LIST = {
  backups: [
    {
      backup_id: BACKUP_ID,
      encrypted: false,
      created_at: "2026-05-30T03:15:00+00:00",
      total_size_bytes: 1048576,
      locations: ["local"],
    },
  ],
};

const PREVIEW = {
  backup_id: BACKUP_ID,
  encrypted: false,
  created_at: "2026-05-30T03:15:00+00:00",
  status: "completed",
  total_size_bytes: 1048576,
  artifacts: [
    { name: "postgres", kind: "pg_dump", size_bytes: 524288, source: null },
    { name: "minio_data.tar.gz", kind: "volume_tar", size_bytes: 524288, source: "minio_data" },
  ],
  per_tenant_available: true,
  tenant_scoped_tables: ["teams", "projects", "plans", "tasks"],
};

async function setup(
  page: Page,
  identity: typeof SYSTEM_ADMIN | typeof TENANT_USER = SYSTEM_ADMIN,
): Promise<void> {
  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route(`${API}/me`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(identity),
    }),
  );

  await page.route(`${API}/admin/backup/restore/backups`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(BACKUP_LIST),
    }),
  );

  await page.route(`${API}/admin/backup/restore/backups/${BACKUP_ID}/preview`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PREVIEW),
    }),
  );
}

// ---------------------------------------------------------------------------
// List + preview render.
// ---------------------------------------------------------------------------
test("system admin sees the backup list and can preview a bundle", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/backup/restore", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("restore-page")).toBeVisible();
  await expect(page.getByTestId(`restore-backup-${BACKUP_ID}`)).toBeVisible();

  await page.getByTestId(`restore-backup-${BACKUP_ID}`).click();
  await expect(page.getByTestId("restore-preview")).toBeVisible();
  await expect(page.getByTestId("restore-preview-id")).toContainText(BACKUP_ID);
  await expect(page.getByTestId("restore-preview-artifacts")).toContainText("postgres");
});

// ---------------------------------------------------------------------------
// Double confirmation — the submit stays disabled until the exact token is typed.
// ---------------------------------------------------------------------------
test("the full-restore confirm requires the exact bundle id token", async ({ page }) => {
  await setup(page);

  let posted: Record<string, unknown> = {};
  await page.route(`${API}/admin/backup/restore`, (route: Route) => {
    posted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "job-123",
        backup_id: BACKUP_ID,
        tenant_id: null,
        kind: "full",
      }),
    });
  });
  await page.route(`${API}/admin/backup/restore/jobs/job-123`, (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "job-123",
        state: "SUCCESS",
        progress: null,
        result: { backup_id: BACKUP_ID },
        error: null,
      }),
    }),
  );

  await page.goto("/admin/backup/restore", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`restore-backup-${BACKUP_ID}`).click();
  await page.getByTestId("restore-open-confirm").click();

  await expect(page.getByTestId("restore-confirm-dialog")).toBeVisible();
  await expect(page.getByTestId("restore-confirm-token")).toContainText(BACKUP_ID);

  // A wrong token keeps the submit disabled (no single-click destructive run).
  await page.getByTestId("restore-confirm-input").fill("nope");
  await expect(page.getByTestId("restore-confirm-submit")).toBeDisabled();

  // The exact token enables it; clicking enqueues the job (full restore).
  await page.getByTestId("restore-confirm-input").fill(BACKUP_ID);
  await expect(page.getByTestId("restore-confirm-submit")).toBeEnabled();
  await page.getByTestId("restore-confirm-submit").click();

  await expect.poll(() => posted.confirm).toBe(BACKUP_ID);
  expect(posted.tenant_id).toBeNull();
  await expect(page.getByTestId("restore-job-state")).toContainText("SUCCESS");
  await expect(page.getByTestId("restore-job-success")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Per-tenant restore — the confirm token is `<tenant_id>@<backup_id>`.
// ---------------------------------------------------------------------------
test("a per-tenant restore requires the tenant-scoped confirm token", async ({ page }) => {
  await setup(page);

  let posted: Record<string, unknown> = {};
  await page.route(`${API}/admin/backup/restore`, (route: Route) => {
    posted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "job-456",
        backup_id: BACKUP_ID,
        tenant_id: TENANT_ID,
        kind: "per_tenant",
      }),
    });
  });
  await page.route(`${API}/admin/backup/restore/jobs/job-456`, (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: "job-456",
        state: "PROGRESS",
        progress: { phase: "restoring", message: `restoring tenant ${TENANT_ID}` },
        result: null,
        error: null,
      }),
    }),
  );

  await page.goto("/admin/backup/restore", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`restore-backup-${BACKUP_ID}`).click();

  await page.getByTestId("restore-kind-per-tenant").check();
  await page.getByTestId("restore-tenant-id").fill(TENANT_ID);
  await expect(page.getByTestId("restore-tenant-tables")).toContainText("projects");

  await page.getByTestId("restore-open-confirm").click();
  const expectedToken = `${TENANT_ID}@${BACKUP_ID}`;
  await expect(page.getByTestId("restore-confirm-token")).toContainText(expectedToken);

  await page.getByTestId("restore-confirm-input").fill(expectedToken);
  await page.getByTestId("restore-confirm-submit").click();

  await expect.poll(() => posted.confirm).toBe(expectedToken);
  expect(posted.tenant_id).toBe(TENANT_ID);
  await expect(page.getByTestId("restore-job-message")).toContainText("restoring tenant");
});

// ---------------------------------------------------------------------------
// RBAC — a plain tenant user cannot reach the restore workspace.
// ---------------------------------------------------------------------------
test("a plain tenant user sees the forbidden notice", async ({ page }) => {
  await setup(page, TENANT_USER);
  await page.goto("/admin/backup/restore", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("restore-forbidden")).toBeVisible();
  await expect(page.getByTestId("restore-workspace")).toHaveCount(0);
});
