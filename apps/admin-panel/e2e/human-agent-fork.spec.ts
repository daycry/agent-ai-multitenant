import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the Human Agents gallery — CLONE-AND-FORK flow (Plan 16 task_16_07).
 *
 * The "Plantillas globales" tab lists the platform's global Human-Agent
 * templates (Security Reviewer Senior, Brand Lead, DBA Senior, Legal Reviewer,
 * UX Lead). "Clonar y forkar" copies a template into the caller's tenant —
 * forked, never linked (Plan 16 Decisiones Clave) — and it then shows up in the
 * tenant's own list.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET  /me                                   — a TENANT ADMIN / a plain USER
 *   - GET  /human-agents                          — the tenant's list (empty, then 1)
 *   - GET  /human-agents/templates                — one global template
 *   - GET  /human-agents/assignable-users         — empty
 *   - POST /human-agents/templates/{id}/clone     — returns the tenant-owned fork
 *
 * NOTE: WRITTEN but NOT run as part of task_16_07 — PENDING HUMAN VERIFICATION
 * (needs a browser + the admin-panel dev server).
 * Run with `npx playwright test e2e/human-agent-fork.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const TEMPLATE_ID = "tmpl-legal-1";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@platform.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const PLAIN_USER = {
  ...TENANT_ADMIN,
  user_id: "88888888-0000-0000-0000-000000000088",
  email: "user@platform.test",
  full_name: "Plain User",
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_user", is_active: true },
  ],
};

const TEMPLATE = {
  id: TEMPLATE_ID,
  tenant_id: "00000000-0000-0000-0000-000000000001",
  name: "Legal Reviewer",
  description: "Revisor legal global.",
  avatar_url: null,
  agent_type: "human",
  role: "reviewer",
  scope: "global_builtin",
  is_template: true,
  forked_from_agent_id: null,
  config: null,
};

const FORK = {
  id: "ha-fork-1",
  tenant_id: TENANT_ID,
  name: "Legal Reviewer",
  description: "Revisor legal global.",
  avatar_url: null,
  agent_type: "human",
  role: "reviewer",
  scope: "global_tenant_template",
  is_template: true,
  forked_from_agent_id: TEMPLATE_ID,
  config: {
    id: "cfg-fork-1",
    agent_id: "ha-fork-1",
    assignment_mode: "specific_user",
    assigned_user_id: null,
    hourly_rate: null,
    hourly_rate_currency: null,
    notification_channels: ["email"],
    acceptance_timeout_hours: 72,
    escalation_target_user_id: null,
    expected_response_time_hours: 24,
    expected_execution_time_hours: null,
  },
};

async function setup(
  page: Page,
  identity: typeof TENANT_ADMIN | typeof PLAIN_USER = TENANT_ADMIN,
  opts: { onClone?: () => void } = {},
): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  let forked = false;

  await page.route(apiRoute("/me"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(identity),
    }),
  );

  await page.route(apiRoute("/human-agents/templates**"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([TEMPLATE]),
    }),
  );

  await page.route(apiRoute("/human-agents/assignable-users**"), (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  await page.route(apiRoute("/human-agents"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: forked ? JSON.stringify([FORK]) : "[]",
    }),
  );

  // ORDEN, no capricho: Playwright resuelve las rutas de la MÁS RECIENTE a la
  // más antigua, y `**/human-agents/templates**` casa también con
  // `.../templates/{id}/clone` (`**` cruza las barras). Registrada antes, la
  // lista de plantillas se comía el POST del fork: `onClone` no llegaba a
  // correr nunca y el test esperaba un contador que jamás subía. La específica
  // va la ÚLTIMA para que gane (2026-08-19).
  await page.route(apiRoute("/human-agents/templates/*/clone"), (route) => {
    opts.onClone?.();
    forked = true;
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(FORK),
    });
  });
}

test("the global template catalog lists the platform templates", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("tab-templates").click();
  await expect(page.getByTestId(`ha-template-${TEMPLATE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`ha-clone-${TEMPLATE_ID}`)).toBeVisible();
});

test("clone-and-fork copies the template into the tenant list", async ({ page }) => {
  let cloneCalls = 0;
  await setup(page, TENANT_ADMIN, { onClone: () => (cloneCalls += 1) });
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });

  await page.getByTestId("tab-templates").click();
  await page.getByTestId(`ha-clone-${TEMPLATE_ID}`).click();
  await expect.poll(() => cloneCalls).toBe(1);

  // The forked, tenant-owned agent now shows in the tenant's own list.
  await page.getByTestId("tab-mine").click();
  await expect(page.getByTestId("human-agent-ha-fork-1")).toBeVisible();
});

test("a plain tenant user cannot clone (no clone button)", async ({ page }) => {
  await setup(page, PLAIN_USER);
  await page.goto("/admin/human-agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("tab-templates").click();
  // The template card renders, but the clone button is gated by RoleGuard.
  await expect(page.getByTestId(`ha-template-${TEMPLATE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`ha-clone-${TEMPLATE_ID}`)).toHaveCount(0);
});
