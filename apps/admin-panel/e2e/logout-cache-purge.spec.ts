import { expect, test } from "@playwright/test";
import { CSRF_COOKIE, TENANT_STORAGE_KEY, seedSession } from "./helpers/session";

/**
 * La caché de TanStack no sobrevive a un cambio de identidad (`task_prod09_11`,
 * hallazgo frontend-4 — `auto_prod09_11_a`).
 *
 * El `QueryClient` vive en el layout RAÍZ, que **no se desmonta al cerrar
 * sesión**: `router.replace('/login')` es una navegación de cliente, el
 * documento es el mismo y el heap también. Sin `queryClient.clear()`, entrar
 * como otro usuario en la misma pestaña le pintaba los datos del anterior — y
 * no «un instante»: `/me` tiene `staleTime` de 5 minutos, así que al remontar
 * el header la caché contesta con el usuario SALIENTE y ni siquiera dispara un
 * refetch. El error se queda en pantalla hasta que algo lo invalide.
 *
 * Por qué esto tiene que ser un e2e y no un unitario: lo que falla es la
 * SUPERVIVENCIA del cliente entre dos montajes del árbol de React en un mismo
 * documento. Un test de `purgeSessionCache()` con un `QueryClient` de mentira
 * pasa igual de verde con el bug puesto.
 *
 * ## Cómo se evita el falso verde
 *
 * 1. **El `/me` del usuario ENTRANTE llega tarde a propósito** (3 s). Así, si
 *    la caché sobreviviese, el usuario saliente estaría pintado justo cuando
 *    miramos; y si se purga, lo que hay es el estado de carga.
 * 2. **La comprobación negativa es una lectura ÚNICA**, no un
 *    `expect().not.toHaveAttribute(...)`. Los matchers de Playwright
 *    reintentan: uno negativo se pondría verde en cuanto el usuario entrante
 *    sustituyera al saliente, que es exactamente el bug que buscamos.
 *
 * ## Trampas heredadas de `session-expiry-401.spec.ts`
 *
 * - `page.route` se evalúa en orden INVERSO al de registro: el genérico va
 *   primero y los concretos después.
 * - El login real contesta con `Set-Cookie` de sesión; en la suite mockeada hay
 *   que sembrarla dentro del handler, o `middleware.ts` rebota `/admin/*` al
 *   login y parece un fallo de la app.
 * - `next dev` compila cada ruta en el primer aterrizaje: de ahí los timeouts
 *   explícitos.
 */

const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

/** Holgura para la primera navegación de cliente bajo `next dev`. */
const FIRST_NAVIGATION = 45_000;
const SLOW_TEST = 150_000;
/** Retraso del `/me` entrante: la ventana en la que el bug sería visible. */
const INCOMING_ME_DELAY_MS = 3_000;

interface FakeUser {
  user_id: string;
  email: string;
  full_name: string;
  is_system_admin: boolean;
  is_system_owner: boolean;
  is_active: boolean;
  memberships: never[];
  active_tenant_id: null;
}

function fakeUser(id: string, email: string, fullName: string): FakeUser {
  return {
    user_id: id,
    email,
    full_name: fullName,
    is_system_admin: true,
    is_system_owner: true,
    is_active: true,
    memberships: [],
    active_tenant_id: null,
  };
}

const OUTGOING = fakeUser(
  "00000000-0000-0000-0000-0000000000a1",
  "alice@example.com",
  "Alice Admin",
);
const INCOMING = fakeUser(
  "00000000-0000-0000-0000-0000000000b2",
  "bruno@example.com",
  "Bruno Admin",
);

function json(body: unknown, status = 200) {
  return { status, contentType: "application/json", body: JSON.stringify(body) };
}

/** Intercepta por PATHNAME exacto: un glob de recurso intercepta también la
 *  navegación a una página que acabe igual (gotcha
 *  `playwright-route-glob-intercepts-navigation.md`). */
function onApiPath(pathname: string) {
  return (url: URL) => url.origin === API_ORIGIN && url.pathname === pathname;
}

test("cerrar sesión y entrar como otro usuario no pinta los datos del anterior", async ({
  page,
}) => {
  test.setTimeout(SLOW_TEST);
  await seedSession(page, { tenantId: "11111111-0000-0000-0000-000000000001" });

  // Quién contesta a `/me`, y cuánto tarda. Mutables: el mismo handler sirve al
  // usuario saliente y al entrante, que es lo que hace observable la caché.
  let whoAmI: FakeUser = OUTGOING;
  let meDelayMs = 0;

  // Genérico primero (los concretos, registrados después, tienen precedencia).
  await page.route(
    (url) => url.origin === API_ORIGIN,
    (route) => route.fulfill(json([])),
  );
  // El dashboard hace `health.data?.services.length`: con un `[]` genérico
  // revienta el render y la navegación se deshace, que se lee igual que "el
  // router no navega".
  await page.route(onApiPath("/admin/system-health"), (route) =>
    route.fulfill(json({ services: [] })),
  );
  await page.route(onApiPath("/auth/sso/providers"), (route) => route.fulfill(json([])));
  await page.route(onApiPath("/me"), async (route) => {
    if (meDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, meDelayMs));
    await route.fulfill(json(whoAmI));
  });
  await page.route(onApiPath("/auth/logout"), (route) => route.fulfill(json({ status: "ok" })));
  await page.route(onApiPath("/auth/login"), async (route) => {
    await seedSession(page);
    await route.fulfill(
      json({ access_token: "irrelevante", token_type: "bearer", expires_in: 900 }),
    );
  });
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

  await page.goto("/admin/dashboard");
  await expect(page.getByTestId("user-menu")).toHaveAttribute("title", OUTGOING.full_name, {
    timeout: FIRST_NAVIGATION,
  });

  // A partir de aquí contesta el usuario entrante, y lo hace tarde.
  whoAmI = INCOMING;
  meDelayMs = INCOMING_ME_DELAY_MS;

  await page.getByTestId("user-menu").click();
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/login/, { timeout: FIRST_NAVIGATION });

  await page.getByLabel("Email").fill(INCOMING.email);
  await page.getByLabel(/^(password|contraseña)$/i).fill("longenoughpw");
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();

  await expect(page).toHaveURL(/\/admin\/dashboard/, { timeout: FIRST_NAVIGATION });
  await expect(page.getByTestId("admin-header")).toBeVisible({ timeout: FIRST_NAVIGATION });

  // LA aserción del test, y por eso es una lectura única sin reintento: en este
  // instante el `/me` de Bruno sigue en vuelo. Con la caché purgada el header
  // está en su estado sin usuario; sin purgar, aquí pone "Alice Admin".
  const whileIncomingIsInFlight = await page.getByTestId("user-menu").getAttribute("title");
  expect(whileIncomingIsInFlight).not.toBe(OUTGOING.full_name);

  // Y el viaje se cierra: cuando el `/me` lento aterriza, manda el entrante.
  await expect(page.getByTestId("user-menu")).toHaveAttribute("title", INCOMING.full_name, {
    timeout: FIRST_NAVIGATION,
  });
});

test("cerrar sesión suelta también el tenant activo y la cookie CSRF", async ({ page }) => {
  test.setTimeout(SLOW_TEST);
  const tenantId = "11111111-0000-0000-0000-000000000001";
  await seedSession(page, { tenantId });

  await page.route(
    (url) => url.origin === API_ORIGIN,
    (route) => route.fulfill(json([])),
  );
  await page.route(onApiPath("/admin/system-health"), (route) =>
    route.fulfill(json({ services: [] })),
  );
  await page.route(onApiPath("/auth/sso/providers"), (route) => route.fulfill(json([])));
  await page.route(onApiPath("/me"), (route) => route.fulfill(json(OUTGOING)));
  await page.route(onApiPath("/auth/logout"), (route) => route.fulfill(json({ status: "ok" })));

  await page.goto("/admin/dashboard");
  await expect(page.getByTestId("user-menu")).toHaveAttribute("title", OUTGOING.full_name, {
    timeout: FIRST_NAVIGATION,
  });

  await page.getByTestId("user-menu").click();
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/login/, { timeout: FIRST_NAVIGATION });

  // El tenant elegido no puede sobrevivir al cambio de identidad: quien entre
  // después en esta pestaña actuaría sobre el tenant del anterior.
  const storedTenant = await page.evaluate(
    (key) => window.localStorage.getItem(key),
    TENANT_STORAGE_KEY,
  );
  expect(storedTenant).toBeNull();

  // Y la mitad legible de la sesión: es lo que `hasSession()` consulta, así que
  // dejarla puesta hace que el panel se crea dentro después del logout.
  const cookies = await page.context().cookies();
  expect(cookies.find((c) => c.name === CSRF_COOKIE)).toBeUndefined();
});

/** El picker de tenant y el `resetQueries` que trae son la OTRA mitad de
 *  `task_prod09_11`; viven en `lib/tenant-context.tsx` y se cubren aparte
 *  (`lib/session-cache.ts` + los unitarios del contexto). Aquí no se duplican:
 *  este fichero es el del cambio de IDENTIDAD, que es donde la caché
 *  superviviente filtra datos de otra persona. */
export {};
