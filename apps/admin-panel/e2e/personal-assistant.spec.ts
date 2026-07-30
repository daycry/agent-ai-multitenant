import { expect, test, type Page } from "@playwright/test";

/**
 * Plan 10 task_10_14 — Personal assistant (Tenant-Admin-only, toggle-gated).
 *
 * PENDING HUMAN VERIFICATION: written, NOT run in this environment (no
 * browser / node-playwright runtime here). A human runs this once the
 * assistant UI (task_10_15) ships and the dev stack + admin-panel are up.
 *
 * Pre-conditions (caller's responsibility):
 *   - docker compose stack is up; api-server on http://localhost:8001 with
 *     CORS allowing http://localhost:3000; admin-panel dev server on :3000.
 *   - A Tenant Admin user exists in a tenant whose
 *     `personal_assistant_enabled` toggle is ON.
 *   - A `tenant_user` (member) exists in the same tenant for the deny case.
 *
 * Backend access constraints this UI must honour (already enforced + tested
 * server-side in tests/integration/test_personal_assistant.py):
 *   - role=admin only (member -> 403, UI hides/disables the assistant).
 *   - toggle off -> 403/disabled.
 *   - read tools are tenant-scoped (RLS) — never another tenant's data.
 */

const ADMIN_EMAIL = process.env.E2E_TENANT_ADMIN_EMAIL ?? "admin@tenant.example.com";
const ADMIN_PASSWORD = process.env.E2E_TENANT_ADMIN_PASSWORD ?? "longenoughpw";
const MEMBER_EMAIL = process.env.E2E_TENANT_MEMBER_EMAIL ?? "member@tenant.example.com";
const MEMBER_PASSWORD = process.env.E2E_TENANT_MEMBER_PASSWORD ?? "longenoughpw";

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel(/^(password|contraseña)$/i).fill(password);
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();
  await expect(page).toHaveURL(/\/admin\//);
}

test("tenant admin can ask the assistant and get a cross-project answer", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/assistant");
  await expect(page.getByTestId("assistant-chat")).toBeVisible();

  await page.getByTestId("assistant-input").fill("¿Qué planes tengo pendientes de aprobación?");
  await page.getByTestId("assistant-send").click();

  // The assistant answer bubble appears (the answer is grounded on the
  // cross-project read tools server-side).
  await expect(page.getByTestId("assistant-answer").first()).toBeVisible();
});

test("member does not see the assistant (Tenant-Admin-only)", async ({ page }) => {
  await login(page, MEMBER_EMAIL, MEMBER_PASSWORD);

  await page.goto("/admin/assistant");
  // The UI must reflect the backend 403: either hidden nav + redirect, or a
  // visible "no access" state. Assert the chat input is NOT reachable.
  await expect(page.getByTestId("assistant-input")).toHaveCount(0);
});

test("tenant admin can customise the assistant identity", async ({ page }) => {
  await login(page, ADMIN_EMAIL, ADMIN_PASSWORD);

  await page.goto("/admin/assistant/settings");
  await expect(page.getByTestId("assistant-identity-form")).toBeVisible();

  await page.getByTestId("assistant-name").fill("Aria");
  await page.getByTestId("assistant-language").selectOption("en");
  await page.getByTestId("assistant-identity-save").click();

  await expect(page.getByTestId("assistant-identity-saved")).toBeVisible();
});
