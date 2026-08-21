import { expect, test, type Page, type Route } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * Modo voz del córtex — botón + tarjeta en `/admin/cortex` (System-Owner-only,
 * Córtex F5, ADR 0073 voz + 0075 afecto).
 *
 * Espejo de la e2e del córtex (`cortex.spec.ts`): el backend es la barrera real
 * (`_is_db_system_owner` en el WS `/ws/owner/cortex/voice`, 1008 si no eres owner
 * — ADR 0074); esta página lo refleja en UX. Como el modo voz vive DENTRO de la
 * página del chat del córtex (gated por `isSystemOwner`), un no-owner ve
 * `cortex-no-access` y NUNCA el botón de voz.
 *
 * El test corre 100% offline:
 *   - GET /me                          — owner vs tenant_admin no-owner
 *   - GET /owner/cortex/conversations  — lista de hilos (vacía)
 *
 * El WebSocket de voz (`/ws/owner/cortex/voice`) NO se mockea: sin stack el socket
 * simplemente no abre y el componente lo tolera (estado de error "servicio de voz
 * no disponible"). El bucle de audio real (mic + STT/TTS) se verifica en el
 * navegador con el stack levantado; el mapeo afecto→avatar se cubre en el unit
 * test `avatarStyleFromAffect` / `parseVoiceAffectFrame` de `lib/cortex.test.ts`.
 *
 * EJECUTADA Y EN VERDE el 2026-08-19 (`npx playwright test e2e/cortex-voice.spec.ts`,
 * 2 passed, dos pasadas seguidas). La cabecera decía «WRITTEN, NOT run …
 * PENDING HUMAN VERIFICATION» desde que se escribió: una spec que nunca ha
 * corrido no es cobertura, es una intención — y el rojo se lo habría encontrado
 * el operador. Lo que sigue pendiente de un humano NO es esta spec sino el QA
 * visual del avatar en navegador (ES+EN, latencia de Kokoro), que ningún test
 * automático puede dar por bueno.
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
  user_id: "00000000-0000-0000-0000-0000000000aa",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  is_system_owner: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockMe(page: Page, me: unknown) {
  await seedSession(page);
  await page.route("**/me", (route) => json(route, me));
}

test("system owner sees the voice toggle and opens the voice card", async ({ page }) => {
  await mockMe(page, OWNER);
  await page.route("**/owner/cortex/conversations", (route) => json(route, []));

  await page.goto("/admin/cortex", { waitUntil: "domcontentloaded" });

  // El botón de "Modo voz" está visible; la videollamada no, hasta pulsar.
  //
  // Auditoría del córtex 2026-07-27 (F5.C4): estas aserciones apuntaban a
  // `cortex-voice-card`, un testid que NO existe en la app — sólo en esta spec.
  // El test no podía pasar nunca; y como su cabecera decía «PENDING HUMAN
  // VERIFICATION», el rojo se lo habría encontrado el operador. El testid real
  // lo emite `VoiceCallShell` a partir de `testidPrefix="cortex-voice"`:
  // `cortex-voice-call` ES la tarjeta (la videollamada a pantalla completa).
  await expect(page.getByTestId("cortex-voice-toggle")).toBeVisible();
  await expect(page.getByTestId("cortex-voice-call")).toHaveCount(0);

  await page.getByTestId("cortex-voice-toggle").click();

  await expect(page.getByTestId("cortex-voice-call")).toBeVisible();
  // El botón para iniciar la videollamada de voz está presente.
  await expect(page.getByTestId("cortex-voice-connect")).toBeVisible();
});

test("a tenant admin who is NOT the owner sees no-access and no voice toggle", async ({ page }) => {
  await mockMe(page, TENANT_ADMIN_NOT_OWNER);
  await page.route("**/owner/cortex/**", (route) => json(route, { detail: "forbidden" }, 403));

  await page.goto("/admin/cortex", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("cortex-no-access")).toBeVisible();
  await expect(page.getByTestId("cortex-voice-toggle")).toHaveCount(0);
  await expect(page.getByTestId("cortex-voice-call")).toHaveCount(0);
});
