import { expect, test, type Page } from "@playwright/test";
import { CSRF_COOKIE, SESSION_COOKIE, clearSession } from "./helpers/session";

/**
 * SSO de punta a punta: del IdP al panel CON sesión (`task_prod09_09`,
 * hallazgo frontend-1 — `auto_prod09_09_b`).
 *
 * El defecto, textual de la auditoría: el callback OIDC contestaba un
 * `LoginResponse` en JSON crudo, así que quien se autenticaba en su IdP acababa
 * mirando `{"access_token": "...", "token_type": "bearer", ...}` en el
 * navegador. El flujo estaba completo en el servidor y **no tenía último
 * tramo**. Ahora el callback pone la cookie y responde 303 al panel.
 *
 * ## Qué prueba ESTE fichero y qué no
 *
 * La mitad del servidor —que el callback devuelve 303 y no JSON, que la cookie
 * es `HttpOnly+Secure`, y las cuatro formas de open-redirect que
 * `sso_landing_url()` rechaza— la cubre
 * `tests/integration/test_sso_callback_redirect.py` con un IdP falso de verdad.
 * Aquí se prueba lo que solo existe en un navegador y ningún test de servidor
 * puede afirmar:
 *
 *   1. que el navegador **sigue** el 303 y **se queda con la cookie** en el
 *      salto entre el origen de la API y el del panel;
 *   2. que `/auth/callback` no es una pantalla muerta: resuelve el tenant y
 *      encamina, igual que hace el login por contraseña;
 *   3. que una resolución que falla acaba en `/login` y no en un spinner
 *      eterno — que es exactamente el modo de fallo que esta tarea existe para
 *      quitar, solo que trasladado una pantalla más allá.
 *
 * El 303 se emula con `route.fulfill`, no con el api-server: la suite mockeada
 * no levanta backend. La emulación es fiel en lo que importa porque es el
 * NAVEGADOR quien procesa la respuesta — sigue el `Location` y aplica el
 * `Set-Cookie` él solito. Y funciona sin CORS porque es una navegación de
 * primer nivel entre dos puertos del MISMO host: las cookies ignoran el puerto,
 * así que `localhost:8001` y `localhost:3000` comparten tarro.
 */

const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
const PANEL_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3000";

/** Holgura para la primera navegación de cliente bajo `next dev`. */
const FIRST_NAVIGATION = 45_000;
const SLOW_TEST = 150_000;

const CURRENT_USER = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "worker@acme.test",
  full_name: "Worker",
  is_system_admin: true,
  is_system_owner: true,
  is_active: true,
  memberships: [],
  active_tenant_id: null,
};

function json(body: unknown, status = 200) {
  return { status, contentType: "application/json", body: JSON.stringify(body) };
}

/** Intercepta por PATHNAME exacto: un glob de recurso intercepta también la
 *  navegación a una página que acabe igual (gotcha
 *  `playwright-route-glob-intercepts-navigation.md`). */
function onApiPath(pathname: string) {
  return (url: URL) => url.origin === API_ORIGIN && url.pathname === pathname;
}

/**
 * El callback del api-server, tal y como responde desde el ADR 0133: 303 al
 * panel + la sesión en cookie. Nada de cuerpo — si aquí hubiera un JSON, el
 * navegador lo pintaría, que es el bug original.
 *
 * Las dos cookies van en UNA cabecera separadas por salto de línea: el mapa de
 * `route.fulfill` es `Record<string,string>` y no admite dos `set-cookie`.
 */
async function stubOidcCallback(page: Page): Promise<void> {
  const setCookie = [
    `${SESSION_COOKIE}=sso-session-token; Path=/; HttpOnly; SameSite=Lax`,
    `${CSRF_COOKIE}=sso-csrf-token; Path=/; SameSite=Lax`,
  ].join("\n");
  await page.route(onApiPath("/auth/sso/oidc/callback"), (route) =>
    route.fulfill({
      status: 303,
      headers: {
        location: `${PANEL_ORIGIN}/auth/callback`,
        "set-cookie": setCookie,
      },
      body: "",
    }),
  );
}

/** Todo lo que el panel pide nada más aterrizar, con la FORMA que espera.
 *  El dashboard hace `health.data?.services.length`: un `[]` genérico revienta
 *  el render y deshace la navegación, que se lee igual que "no navega". */
async function stubPanelReads(page: Page): Promise<void> {
  await page.route(
    (url) => url.origin === API_ORIGIN,
    (route) => route.fulfill(json([])),
  );
  await page.route(onApiPath("/admin/system-health"), (route) =>
    route.fulfill(json({ services: [] })),
  );
  await page.route(onApiPath("/auth/sso/providers"), (route) => route.fulfill(json([])));
  await page.route(onApiPath("/me"), (route) => route.fulfill(json(CURRENT_USER)));
}

function resolution(state: string, memberships: unknown[] = []) {
  return json({
    state,
    memberships,
    access_token: null,
    token_type: null,
    expires_in: null,
  });
}

test("el navegador sigue el 303 del callback y entra en el panel, nunca en un JSON", async ({
  page,
}) => {
  test.setTimeout(SLOW_TEST);
  await clearSession(page);
  await stubPanelReads(page);
  await page.route(onApiPath("/auth/session/resolve"), (route) =>
    route.fulfill(resolution("admin")),
  );
  await stubOidcCallback(page);

  // Volvemos del IdP: esto es literalmente la URL a la que el proveedor
  // redirige al usuario.
  await page.goto(`${API_ORIGIN}/auth/sso/oidc/callback?code=fake-auth-code&state=fake-state`);

  // El último tramo que faltaba: se aterriza AUTENTICADO en el panel.
  await expect(page).toHaveURL(/\/admin\/dashboard/, { timeout: FIRST_NAVIGATION });
  await expect(page.getByTestId("admin-header")).toBeVisible({ timeout: FIRST_NAVIGATION });

  // Y el síntoma exacto de la auditoría no reaparece por ningún lado.
  await expect(page.locator("body")).not.toContainText("access_token");

  // La credencial viajó en el salto entre orígenes, y viajó como debe.
  const cookies = await page.context().cookies();
  const session = cookies.find((c) => c.name === SESSION_COOKIE);
  expect(session).toBeTruthy();
  expect(session?.httpOnly).toBe(true);
  // Nada con forma de JWT en `localStorage`: el handoff SSO no reabre por
  // detrás la puerta que el ADR 0133 cerró en el login por contraseña.
  const stored = await page.evaluate(() => Object.keys(window.localStorage));
  expect(stored).not.toContain("agentic.token");
});

test("un usuario SSO con varios tenants aterriza en el selector, no en el dashboard", async ({
  page,
}) => {
  test.setTimeout(SLOW_TEST);
  await clearSession(page);
  await stubPanelReads(page);
  await page.route(onApiPath("/auth/session/resolve"), (route) =>
    route.fulfill(
      resolution("multiple", [
        {
          tenant_id: "11111111-0000-0000-0000-000000000001",
          tenant_name: "Acme",
          role: "tenant_admin",
        },
        {
          tenant_id: "22222222-0000-0000-0000-000000000002",
          tenant_name: "Globex",
          role: "tenant_user",
        },
      ]),
    ),
  );
  await stubOidcCallback(page);

  await page.goto(`${API_ORIGIN}/auth/sso/oidc/callback?code=fake-auth-code&state=fake-state`);

  // `/select-tenant` está en el `matcher` de `middleware.ts`, así que llegar
  // hasta aquí demuestra ADEMÁS que la cookie del 303 sobrevivió al salto: sin
  // ella el edge habría rebotado a `/login`.
  await expect(page).toHaveURL(/\/select-tenant/, { timeout: FIRST_NAVIGATION });
});

test("si la resolución falla, el usuario acaba en el login y no en un spinner eterno", async ({
  page,
}) => {
  test.setTimeout(SLOW_TEST);
  await clearSession(page);
  await stubPanelReads(page);
  await page.route(onApiPath("/auth/session/resolve"), (route) =>
    route.fulfill(json({ detail: "boom" }, 500)),
  );
  await stubOidcCallback(page);

  await page.goto(`${API_ORIGIN}/auth/sso/oidc/callback?code=fake-auth-code&state=fake-state`);

  await expect(page).toHaveURL(/\/login/, { timeout: FIRST_NAVIGATION });
  await expect(page.getByTestId("login-brand")).toBeVisible();
});
