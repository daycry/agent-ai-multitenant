import { expect, test, type Page } from "@playwright/test";
import { CSRF_COOKIE, clearSession, seedSession } from "./helpers/session";

/**
 * El 401 global: una sesión muerta devuelve al login, no deja al usuario
 * atascado (`task_prod09_10`, hallazgo frontend-3 — `auto_prod09_10_a`).
 *
 * Antes, cada pantalla pintaba el cuerpo crudo del 401 y ahí se acababa el
 * viaje: sin mensaje, sin botón y sin forma de volver. Ahora `lib/api.ts` trata
 * el 401 en UN sitio, limpia el estado de cliente (cookie CSRF + tenant) y
 * delega en el handler que `app/providers.tsx` cablea a `queryClient.clear()` +
 * `router.replace('/login?next=…')`.
 *
 * Lo que sólo se puede probar en navegador —y por eso el plan pide un e2e— es
 * la CADENA COMPLETA: `apiFetch` → handler inyectado → router de Next → la
 * página de login leyendo su `?next=`. `lib/api.test.ts` cubre el primer
 * eslabón con un handler falso; aquí no hay ningún doble.
 *
 * Los dos casos son igual de importantes:
 *
 *   1. 401 en una pantalla autenticada → rebote a `/login?next=<ruta>`.
 *   2. 401 en `/auth/login` (contraseña mala) → NO rebota. Es la respuesta
 *      normal a un password incorrecto, y recargar `/login` desde `/login` se
 *      come el mensaje de error: el 401 global bien intencionado dejaría el
 *      login inutilizable, sin que nada avisara.
 *
 * ## Dos trampas que costaron una tarde
 *
 * 1. **Precedencia de `page.route`**: Playwright prueba los handlers en orden
 *    INVERSO al de registro, así que el genérico va PRIMERO y los específicos
 *    después. Al revés, el catch-all tapa a los concretos y la pantalla recibe
 *    un cuerpo que no espera (aquí: `[]` donde el dashboard lee `.services`,
 *    que revienta el render y aborta la navegación en curso).
 * 2. **`next dev` compila la ruta al primer aterrizaje.** Una navegación de
 *    cliente a `/login` que nunca se ha visitado tarda entre 5 y 30 s en este
 *    dev-server, y el `toHaveURL` por defecto se rinde a los 5 s: parece que
 *    `router.replace()` no hace nada cuando lo que pasa es que webpack sigue
 *    trabajando. De ahí los timeouts explícitos. En CI no aplica (`next start`
 *    sirve un build ya compilado), pero el timeout generoso no molesta.
 */

const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

/** Holgura para la primera navegación de cliente bajo `next dev` (ver cabecera).
 *  El timeout POR TEST (30 s por defecto) tiene que ser mayor que éste, o el
 *  test muere antes de que la aserción llegue a agotarse. */
const FIRST_NAVIGATION = 45_000;
const SLOW_TEST = 120_000;

/**
 * Un `/me` con la FORMA que el panel espera.
 *
 * No es decorado: `useCurrentUser` hace `user.memberships.find(...)` y
 * `app/admin/dashboard` hace `data.services.length`. Un mock genérico (`[]`)
 * los revienta durante el render, React deshace la navegación y la URL vuelve
 * al punto de partida — que se lee exactamente igual que "el router no navega".
 */
const CURRENT_USER = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "root@example.com",
  full_name: "Root",
  is_system_admin: true,
  is_system_owner: true,
  is_active: true,
  memberships: [],
  active_tenant_id: null,
};

/** Intercepta por PATHNAME exacto, nunca por glob de recurso: en Playwright
 *  1.60 un `**\/me` intercepta también la navegación a una página que acabe
 *  igual (gotcha `playwright-route-glob-intercepts-navigation.md`). */
function onApiPath(pathname: string) {
  return (url: URL) => url.origin === API_ORIGIN && url.pathname === pathname;
}

async function stubPublicProviders(page: Page): Promise<void> {
  await page.route(onApiPath("/auth/sso/providers"), (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) }),
  );
}

function json(body: unknown, status = 200) {
  return { status, contentType: "application/json", body: JSON.stringify(body) };
}

test("un 401 en una pantalla autenticada devuelve al login conservando la ruta", async ({
  page,
}) => {
  test.setTimeout(SLOW_TEST);
  await seedSession(page, { tenantId: "11111111-0000-0000-0000-000000000001" });

  // La sesión ha caducado en el servidor: TODO responde 401. Va primero para
  // que el stub de proveedores (registrado después) tenga precedencia.
  await page.route(
    (url) => url.origin === API_ORIGIN,
    (route) => route.fulfill(json({ detail: "session has been revoked" }, 401)),
  );
  await stubPublicProviders(page);

  await page.goto("/admin/dashboard");

  await expect(page).toHaveURL(/\/login\?next=/, { timeout: FIRST_NAVIGATION });
  // La ruta viaja para poder devolver al usuario donde estaba.
  expect(new URL(page.url()).searchParams.get("next")).toBe("/admin/dashboard");
  // Y el login se ve: el usuario tiene por dónde seguir, que es justo lo que
  // el cuerpo crudo del 401 no daba.
  await expect(page.getByTestId("login-brand")).toBeVisible();

  // El estado de cliente se ha soltado. La cookie CSRF sin sesión detrás no
  // autentica nada, pero el tenant sí importa: sobrevivir significaría que el
  // siguiente que entre en esta pestaña actúa sobre el tenant del anterior.
  const cookies = await page.context().cookies();
  expect(cookies.find((c) => c.name === CSRF_COOKIE)).toBeUndefined();
  const tenant = await page.evaluate(() => window.localStorage.getItem("admin-panel.tenant-id"));
  expect(tenant).toBeNull();
});

test("un 401 de /auth/login se queda en el login y enseña el error", async ({ page }) => {
  await clearSession(page);
  await stubPublicProviders(page);
  await page.route(onApiPath("/auth/login"), (route) =>
    route.fulfill(json({ detail: "invalid credentials" }, 401)),
  );

  await page.goto("/login?next=%2Fadmin%2Fagents");
  await page.getByLabel("Email").fill("root@example.com");
  await page.getByLabel(/^(password|contraseña)$/i).fill("definitely-wrong");
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();

  await expect(page.getByTestId("login-error")).toBeVisible();
  // Sin recarga: el `?next=` sigue en la URL y el mensaje sigue en pantalla.
  expect(new URL(page.url()).searchParams.get("next")).toBe("/admin/agents");
});

test("tras re-autenticarse, el usuario vuelve a la ruta que pedía", async ({ page }) => {
  test.setTimeout(SLOW_TEST);
  await clearSession(page);

  // Genérico primero; los concretos después ganan la precedencia.
  await page.route(
    (url) => url.origin === API_ORIGIN,
    (route) => route.fulfill(json([])),
  );
  await stubPublicProviders(page);
  await page.route(onApiPath("/me"), (route) => route.fulfill(json(CURRENT_USER)));
  await page.route(onApiPath("/auth/login"), (route) =>
    route.fulfill(json({ access_token: "irrelevante", token_type: "bearer", expires_in: 900 })),
  );
  // Un System Admin sin membership: estado "admin" → entra en el panel.
  await page.route(onApiPath("/auth/session/resolve"), (route) =>
    route.fulfill(
      json({
        state: "admin",
        memberships: [],
        access_token: null,
        token_type: null,
        expires_in: null,
      }),
    ),
  );

  await page.goto("/login?next=%2Fadmin%2Fagents");
  await page.getByLabel("Email").fill("root@example.com");
  await page.getByLabel(/^(password|contraseña)$/i).fill("longenoughpw");
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();

  // El viaje se cierra: 401 → /login?next=… → login → la ruta original. Sin
  // esto el `?next=` sería adorno y el usuario aterrizaría siempre en el
  // dashboard, perdiendo lo que estaba haciendo.
  await expect(page).toHaveURL(/\/admin\/agents/, { timeout: FIRST_NAVIGATION });
});
