import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Córtex F3 (bloque 2) — página `/admin/cortex/identity` (System-Owner-only).
 *
 * Espejo de `e2e/cortex.spec.ts`: gated por `is_system_owner` (DB-authoritative
 * en el backend, ADR 0074). Un no-owner ve `cortex-identity-no-access` y NUNCA el
 * formulario; el owner ve el form de onboarding co-diseñado.
 *
 * Mock 100% offline:
 *   - GET /me                       — owner vs tenant_admin no-owner
 *   - GET /owner/cortex/identity    — identidad (onboarding pendiente)
 *   - PUT /owner/cortex/identity    — guarda y devuelve la nueva versión
 *
 * NOTA: WRITTEN, NOT run aquí (no hay browser + admin-panel dev server en este
 * entorno). PENDING HUMAN VERIFICATION — correr con
 * `npx playwright test e2e/cortex-identity.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";

const OWNER = {
  user_id: "00000000-0000-0000-0000-0000000000ee",
  email: "owner@platform.test",
  full_name: "System Owner",
  is_system_admin: true,
  is_system_owner: true,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const TENANT_ADMIN_NOT_OWNER = {
  ...OWNER,
  user_id: "00000000-0000-0000-0000-0000000000aa",
  email: "admin@tenant.test",
  is_system_admin: false,
  is_system_owner: false,
};

const NEUTRAL_TRAITS = {
  openness: 0.5,
  conscientiousness: 0.5,
  extraversion: 0.5,
  agreeableness: 0.5,
  neuroticism: 0.5,
};

const IDENTITY_PENDING = {
  name: "Córtex",
  core_values: [],
  narrative: "",
  language: "es",
  learning_goals: [],
  traits: NEUTRAL_TRAITS,
  mood_baseline: { valence: 0, arousal: 0, dominance: 0 },
  version: 0,
  updated_by: "onboarding",
  onboarded_at: null,
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockMe(page: Page, me: unknown) {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/me", (route) => json(route, me));
}

test("system owner sees the identity form and the pending-onboarding banner", async ({ page }) => {
  await mockMe(page, OWNER);
  await page.route("**/owner/cortex/identity", (route) => {
    if (route.request().method() === "PUT") {
      return json(route, {
        ...IDENTITY_PENDING,
        name: "Atlas",
        core_values: ["honestidad"],
        version: 1,
        updated_by: "owner_override",
        onboarded_at: "2026-06-24T10:00:00Z",
      });
    }
    return json(route, IDENTITY_PENDING);
  });

  await page.goto("/admin/cortex/identity", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("cortex-identity-onboarding")).toBeVisible();
  await expect(page.getByTestId("cortex-identity-name")).toBeVisible();
  await expect(page.getByTestId("cortex-identity-honesty")).toBeVisible();

  await page.getByTestId("cortex-identity-name").fill("Atlas");
  await page.getByTestId("cortex-identity-save").click();
  await expect(page.getByTestId("cortex-identity-saved")).toBeVisible();
});

test("a tenant admin who is NOT the owner sees no-access and no form", async ({ page }) => {
  await mockMe(page, TENANT_ADMIN_NOT_OWNER);
  await page.route("**/owner/cortex/**", (route) => json(route, { detail: "forbidden" }, 403));

  await page.goto("/admin/cortex/identity", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("cortex-identity-no-access")).toBeVisible();
  await expect(page.getByTestId("cortex-identity-name")).toHaveCount(0);
});
