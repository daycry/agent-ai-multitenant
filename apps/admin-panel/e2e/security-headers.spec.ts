import { expect, test, type Page } from "@playwright/test";

/**
 * Las cabeceras de seguridad del panel, servidas de verdad (`task_prod09_15`,
 * hallazgos frontend-6 y frontend-8 — `auto_prod09_15_a`).
 *
 * `lib/security-headers.test.ts` ya comprueba la FUNCIÓN que las construye. Lo
 * que no puede comprobar es que `next.config.js` la cablee y que Next las emita:
 * un `headers()` con un `source` mal escrito, un `module.exports` que se deja
 * la clave, o un `matcher` que no cubre el documento HTML dejan los 23 tests de
 * la unidad en verde y el panel sin una sola cabecera. Por eso el plan pide un
 * e2e y no otro test de unidad: aquí el navegador hace una navegación real y se
 * leen las cabeceras de la RESPUESTA.
 *
 * Se afirma sólo sobre lo que es IDÉNTICO en dev y en producción, porque la
 * suite corre de las dos formas (`next dev` en local, `next start` con
 * `NODE_ENV=production` en CI):
 *
 *   - las cuatro directivas de la CSP baseline, que van EN VIGOR desde el
 *     primer despliegue (`frame-ancestors 'none'` es el antiframing real);
 *   - `X-Frame-Options`, `X-Content-Type-Options` y `Referrer-Policy`;
 *   - la CSP completa en `Report-Only`, que es la calibración que el riesgo 2
 *     del plan exige antes de promoverla (`CSP_ENFORCE=1`).
 *
 * `'unsafe-eval'` NO se afirma: sólo está en dev (React Refresh compila con
 * `eval`), y fijarlo aquí ataría el test a un entorno.
 */

/** Stub del único fetch que hace `/login`, para no depender de un api-server
 *  vivo (y, de paso, para que el subset mockeado de CI recoja este spec). */
async function stubPublicProviders(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === "/auth/sso/providers",
    (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) }),
  );
}

/** Directivas que `buildSecurityHeaders` promete EN VIGOR en todos los entornos. */
const BASELINE = [
  "base-uri 'self'",
  "object-src 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
];

test("el documento HTML llega con las cabeceras de seguridad", async ({ page }) => {
  await stubPublicProviders(page);

  const response = await page.goto("/login");
  expect(response, "la navegación no devolvió respuesta").toBeTruthy();
  const headers = response!.headers();

  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");

  // `poweredByHeader: false`: la versión de Next es reconocimiento gratis.
  expect(headers["x-powered-by"]).toBeUndefined();
});

test("la CSP en vigor cierra clickjacking, <base> y form-action", async ({ page }) => {
  await stubPublicProviders(page);

  const response = await page.goto("/login");
  const csp = response!.headers()["content-security-policy"];

  expect(csp, "no se sirve Content-Security-Policy").toBeTruthy();
  for (const directive of BASELINE) {
    expect(csp).toContain(directive);
  }
  // La baseline NO lleva default-src: eso vive en Report-Only hasta que el
  // operador construya con CSP_ENFORCE=1 (riesgo 2 del plan). Si algún día se
  // promueve, este test avisa de que hay que revisarlo en vez de romperse solo.
  expect(csp).not.toContain("default-src");
});

test("la política completa se reporta sin bloquear, lista para promover", async ({ page }) => {
  await stubPublicProviders(page);

  const response = await page.goto("/login");
  const reportOnly = response!.headers()["content-security-policy-report-only"];

  expect(reportOnly, "no se sirve la CSP de calibración").toBeTruthy();
  expect(reportOnly).toContain("default-src 'self'");
  // Sin nonce, los scripts inline de Flight de Next obligan a 'unsafe-inline';
  // está documentado en lib/security-headers.js y es lo que se calibra.
  expect(reportOnly).toContain("script-src 'self' 'unsafe-inline'");
  expect(reportOnly).toContain("connect-src 'self'");
  expect(reportOnly).toContain("frame-src 'none'");
  for (const directive of BASELINE) {
    expect(reportOnly).toContain(directive);
  }
});

test("una ruta protegida también las lleva (el source cubre todo el panel)", async ({ page }) => {
  // El redirect del middleware a /login es una respuesta más: si `source` no
  // fuese "/(.*)" o el middleware cortocircuitase las cabeceras, aquí faltarían.
  await stubPublicProviders(page);

  const response = await page.goto("/admin/dashboard");

  await expect(page).toHaveURL(/\/login/);
  expect(response!.headers()["x-frame-options"]).toBe("DENY");
  expect(response!.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
});
