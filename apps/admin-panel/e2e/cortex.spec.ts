import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Córtex F1 (Tarea 12) — página `/admin/cortex` (System-Owner-only).
 *
 * Espejo de la e2e del asistente personal pero gated por `is_system_owner`
 * (no por rol de tenant + toggle). El backend es la barrera real
 * (`require_system_owner`, DB-authoritative, 403 si no eres owner — ADR 0074);
 * esta página lo refleja en UX: un no-owner ve `cortex-no-access` y NUNCA el
 * input.
 *
 * El test mockea el backend para correr 100% offline (patrón de
 * `admin-models-prices.spec.ts`):
 *   - GET  /me                          — owner vs tenant_admin no-owner
 *   - GET  /owner/cortex/conversations  — lista de hilos (vacía / con uno)
 *   - GET  /owner/cortex/turns          — turnos del hilo
 *   - POST /owner/cortex/turns          — crea hilo + devuelve respuesta
 *
 * NOTA: WRITTEN, NOT run aquí (no hay browser + admin-panel dev server en este
 * entorno). PENDING HUMAN VERIFICATION — correr con
 * `npx playwright test e2e/cortex.spec.ts`. El backend del córtex F1 debe estar
 * montado para una pasada contra el stack real (Tareas 1–10).
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const CONVERSATION_ID = "cccc1111-0000-0000-0000-0000000000c1";

const OWNER = {
  user_id: "00000000-0000-0000-0000-0000000000ee",
  email: "owner@platform.test",
  full_name: "System Owner",
  is_system_admin: true,
  is_system_owner: true,
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

const TENANT_ADMIN_NOT_OWNER = {
  user_id: "00000000-0000-0000-0000-0000000000aa",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  is_system_owner: false,
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

const CORTEX_ANSWER =
  "## Hilo retomado\n\nRecuerdo tu interés en la **arquitectura hexagonal**. ¿Por dónde seguimos?";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockMe(page: Page, me: unknown) {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/me", (route) => json(route, me));
}

test("system owner sees the córtex input and gets a rendered answer", async ({ page }) => {
  await mockMe(page, OWNER);

  // Empieza sin hilos; el primer POST crea uno y devuelve la respuesta.
  await page.route("**/owner/cortex/conversations", (route) => json(route, []));
  await page.route("**/owner/cortex/turns**", (route) => {
    if (route.request().method() === "POST") {
      return json(route, {
        conversation_id: CONVERSATION_ID,
        answer: CORTEX_ANSWER,
        tools_called: [],
        rounds: 1,
        reasoning_effort: "high",
        degraded: false,
      });
    }
    // GET turns del hilo recién creado.
    return json(route, [
      {
        id: "tttt0001",
        role: "user",
        content: "Retomemos lo de la arquitectura.",
        created_at: "2026-06-24T10:00:00Z",
      },
      {
        id: "tttt0002",
        role: "cortex",
        content: CORTEX_ANSWER,
        created_at: "2026-06-24T10:00:01Z",
        model_id: "claude-sonnet-4-5",
      },
    ]);
  });

  await page.goto("/admin/cortex", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("cortex-chat")).toBeVisible();
  await expect(page.getByTestId("cortex-input")).toBeVisible();

  await page.getByTestId("cortex-input").fill("Retomemos lo de la arquitectura.");
  await page.getByTestId("cortex-send").click();

  // La respuesta del córtex aparece renderizada como markdown.
  await expect(page.getByTestId("cortex-answer").first()).toBeVisible();
});

test("a tenant admin who is NOT the owner sees no-access and no input", async ({ page }) => {
  await mockMe(page, TENANT_ADMIN_NOT_OWNER);
  // Si la página intentara llamar (no debe), el backend devolvería 403.
  await page.route("**/owner/cortex/**", (route) => json(route, { detail: "forbidden" }, 403));

  await page.goto("/admin/cortex", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("cortex-no-access")).toBeVisible();
  await expect(page.getByTestId("cortex-input")).toHaveCount(0);
});
