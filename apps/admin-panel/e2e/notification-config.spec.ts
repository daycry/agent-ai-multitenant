import { expect, test, type Page } from "@playwright/test";

/**
 * Plan 10 task_10_15 — 3-layer notification config UI (channels + preferences).
 *
 * PENDING HUMAN VERIFICATION: written, NOT run in this environment (no
 * browser / node-playwright runtime here). A human runs this once the dev
 * stack + admin-panel are up.
 *
 * Pre-conditions (caller's responsibility):
 *   - docker compose stack is up; api-server on http://localhost:8001 with
 *     CORS allowing http://localhost:3000; admin-panel dev server on :3000.
 *   - A Tenant Admin user exists; a `tenant_user` (member) exists in the same
 *     tenant for the deny case; a System Admin exists for the platform tab.
 *   - At least one channel transport is enabled platform-wide (default: all).
 *
 * Backend constraints this UI must honour (enforced + tested server-side in
 * tests/integration/test_notification_config.py):
 *   - channel/preference writes are tenant_admin only (member -> 403);
 *   - the platform channel-types write is System-Admin only;
 *   - a channel SECRET is never echoed (response carries only has_secret +
 *     secret_source) and is RLS/tenant-isolated.
 */

const ADMIN_EMAIL = process.env.E2E_TENANT_ADMIN_EMAIL ?? "admin@tenant.example.com";
const ADMIN_PASSWORD = process.env.E2E_TENANT_ADMIN_PASSWORD ?? "longenoughpw";
const MEMBER_EMAIL = process.env.E2E_TENANT_MEMBER_EMAIL ?? "member@tenant.example.com";
const MEMBER_PASSWORD = process.env.E2E_TENANT_MEMBER_PASSWORD ?? "longenoughpw";
const SYSADMIN_EMAIL = process.env.E2E_SYSTEM_ADMIN_EMAIL ?? "sys@platform.example.com";
const SYSADMIN_PASSWORD = process.env.E2E_SYSTEM_ADMIN_PASSWORD ?? "longenoughpw";

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\//);
}

test("tenant admin creates a channel; the secret is never shown back", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/notifications");
  await expect(page.getByTestId("notification-config-page")).toBeVisible();

  // Channels tab is the default.
  await page.getByTestId("channel-create-button").click();
  await expect(page.getByTestId("channel-dialog")).toBeVisible();

  await page.getByTestId("channel-form-name").fill("Ops bot");
  await page.getByTestId("channel-form-type").selectOption("telegram");
  await page.getByTestId("channel-form-config").fill('{ "chat_id": "12345" }');
  await page.getByTestId("channel-form-secret").fill("super-secret-bot-token");
  await page.getByTestId("channel-form-submit").click();

  // The new channel card appears with a "secret set" badge — and the clear
  // secret never appears anywhere on the page.
  await expect(page.getByTestId("channels-list")).toContainText("Ops bot");
  await expect(page.locator("body")).not.toContainText("super-secret-bot-token");
});

test("tenant admin mutes budget_alert on a channel via the preference matrix", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/notifications");
  await page.getByTestId("tab-preferences").click();
  await expect(page.getByTestId("preferences-tab")).toBeVisible();

  // Toggle budget_alert off for the first configured transport (telegram in
  // the create test above). The change persists via the upsert endpoint.
  const cell = page.getByTestId("preference-budget_alert-telegram");
  await expect(cell).toBeVisible();
  await cell.uncheck();
  await expect(cell).not.toBeChecked();
});

test("member cannot mutate channels (Tenant-Admin-only writes)", async ({ page }) => {
  await login(page, MEMBER_EMAIL, MEMBER_PASSWORD);

  await page.goto("/admin/notifications");
  // The create button is RoleGuard-gated to tenant_admin, so a member never
  // sees it.
  await expect(page.getByTestId("channel-create-button")).toHaveCount(0);
});

test("system admin sets the globally enabled channel transports", async ({ page }) => {
  await login(page, SYSADMIN_EMAIL, SYSADMIN_PASSWORD);

  await page.goto("/admin/notifications");
  // The platform tab is only rendered for a System Admin.
  await page.getByTestId("tab-platform").click();
  await expect(page.getByTestId("platform-channel-types")).toBeVisible();

  // Disable SMS platform-wide and save.
  await page.getByTestId("platform-type-sms").uncheck();
  await page.getByTestId("platform-save").click();
  await expect(page.getByTestId("platform-save-error")).toHaveCount(0);
});
