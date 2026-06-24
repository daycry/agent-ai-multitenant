import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Panel de Mente — página `/admin/cortex/mind` (System-Owner-only, Córtex F2).
 *
 * Espejo de la e2e del córtex (`cortex.spec.ts`) pero para el dashboard de
 * estado afectivo. El backend es la barrera real (`require_system_owner`,
 * DB-authoritative, 403 si no eres owner — ADR 0074); esta página lo refleja en
 * UX: un no-owner ve `cortex-mind-no-access` y NUNCA los diales.
 *
 * El test mockea el backend para correr 100% offline:
 *   - GET /me                                — owner vs tenant_admin no-owner
 *   - GET /owner/cortex/mind                 — estado afectivo vivo (+ honesty)
 *   - GET /owner/cortex/affect/timeseries    — snapshots para el gráfico
 *   - GET /owner/cortex/episodes             — episódicas emocionales
 *
 * El WebSocket de telemetría (`/ws/owner/cortex/telemetry`) NO se mockea: sin
 * stack el socket simplemente no abre y `useWebSocket` lo tolera (backoff); los
 * diales se alimentan del `/mind` inicial + polling. La actualización en vivo se
 * cubre por el unit test `affectFrameToMind` en `lib/cortex.test.ts`.
 *
 * NOTA: WRITTEN, NOT run aquí (no hay browser + admin-panel dev server en este
 * entorno). PENDING HUMAN VERIFICATION — correr con
 * `npx playwright test e2e/cortex-mind.spec.ts`. El backend del córtex F2 debe
 * estar montado para una pasada contra el stack real.
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

const MIND = {
  valence: 0.42,
  arousal: 0.61,
  dominance: -0.15,
  intensity: 0.55,
  mood_valence: 0.3,
  mood_arousal: 0.5,
  mood_dominance: -0.1,
  mood_label: "concentrado",
  drives: { curiosity: 0.82, bonding: 0.4, coherence: 0.66, competence: 0.55 },
  honesty: {
    note_es: "Modelo computacional de afecto, no sentimientos reales.",
    note_en: "Computational model of affect, not real feelings.",
  },
};

const TIMESERIES = [
  {
    created_at: "2026-06-24T09:00:00Z",
    valence: 0.1,
    arousal: 0.4,
    dominance: 0.0,
    intensity: 0.3,
    mood_valence: 0.1,
    mood_arousal: 0.4,
    mood_dominance: 0.0,
    mood_label: "neutro",
    drives: { curiosity: 0.5, bonding: 0.5, coherence: 0.5, competence: 0.5 },
  },
  {
    created_at: "2026-06-24T10:00:00Z",
    valence: 0.42,
    arousal: 0.61,
    dominance: -0.15,
    intensity: 0.55,
    mood_valence: 0.3,
    mood_arousal: 0.5,
    mood_dominance: -0.1,
    mood_label: "concentrado",
    drives: { curiosity: 0.82, bonding: 0.4, coherence: 0.66, competence: 0.55 },
  },
];

const EPISODES = [
  {
    id: "eeee0001-0000-0000-0000-0000000000e1",
    content: "El owner resolvió un bug difícil y lo celebramos.",
    created_at: "2026-06-24T10:00:01Z",
    mood_label: "satisfecho",
    valence: 0.6,
    arousal: 0.5,
    dominance: 0.2,
    intensity: 0.7,
    appraisal_reason: "Logro de competencia: el problema cedió tras varios intentos.",
  },
];

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

test("system owner sees the mind dials, mood, drives, chart and episodes", async ({ page }) => {
  await mockMe(page, OWNER);

  // El orden importa: rutas más específicas antes que la genérica /mind.
  await page.route("**/owner/cortex/affect/timeseries**", (route) => json(route, TIMESERIES));
  await page.route("**/owner/cortex/episodes**", (route) => json(route, EPISODES));
  await page.route("**/owner/cortex/mind", (route) => json(route, MIND));

  await page.goto("/admin/cortex/mind", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("cortex-mind")).toBeVisible();
  // Honestidad SIEMPRE visible (ADR 0075 §6).
  await expect(page.getByTestId("cortex-mind-honesty")).toBeVisible();
  // Diales PAD + mood.
  await expect(page.getByTestId("pad-valence")).toBeVisible();
  await expect(page.getByTestId("pad-arousal")).toBeVisible();
  await expect(page.getByTestId("pad-dominance")).toBeVisible();
  await expect(page.getByTestId("mood-label")).toContainText("concentrado");
  // Drives.
  await expect(page.getByTestId("drives")).toBeVisible();
  // Gráfico de mood.
  await expect(page.getByTestId("mood-chart")).toBeVisible();
  // Episodios + su motivo de appraisal.
  await expect(page.getByTestId("episodes")).toBeVisible();
  await expect(page.getByText("El owner resolvió un bug difícil")).toBeVisible();
});

test("a tenant admin who is NOT the owner sees no-access and no dials", async ({ page }) => {
  await mockMe(page, TENANT_ADMIN_NOT_OWNER);
  // Si la página intentara llamar (no debe), el backend devolvería 403.
  await page.route("**/owner/cortex/**", (route) => json(route, { detail: "forbidden" }, 403));

  await page.goto("/admin/cortex/mind", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("cortex-mind-no-access")).toBeVisible();
  await expect(page.getByTestId("cortex-mind")).toHaveCount(0);
  await expect(page.getByTestId("pad-valence")).toHaveCount(0);
});
