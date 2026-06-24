/** Helper de login reutilizable para los manuales. */
import { Page, expect } from "@playwright/test";

export type Creds = { email: string; password: string; tenant?: string };

export function credsFromEnv(): Creds {
  return {
    email: process.env.MANUALS_EMAIL ?? "demo@example.com",
    password: process.env.MANUALS_PASSWORD ?? "demo-manuales-pw-2026",
    tenant: process.env.MANUALS_TENANT ?? "Demo Manuales",
  };
}

/**
 * Inicia sesión por la UI real (email/password). Tras el login, el frontend
 * resuelve las membresías: un único tenant entra directo a /admin; varios
 * muestran el selector de tenant (lo elegimos por nombre).
 */
export async function login(page: Page, creds: Creds = credsFromEnv()): Promise<void> {
  // networkidle (no domcontentloaded): hay que esperar a que React HIDRATE los
  // inputs controlados; si se rellenan antes, el onChange no registra el valor,
  // el formulario va vacío y la validación required bloquea el envío (NO se hace
  // POST /auth/login y nos quedamos en /login).
  await page
    .goto("/login", { waitUntil: "networkidle" })
    .catch(() => page.goto("/login", { waitUntil: "load" }));
  const email = page.locator("#email");
  await email.waitFor({ state: "visible" });
  await email.fill(creds.email);
  await page.locator("#password").fill(creds.password);
  // Verificar que React registró los valores; si un render temprano los vació,
  // reintentar antes de enviar.
  if ((await email.inputValue()) !== creds.email) {
    await email.fill(creds.email);
    await page.locator("#password").fill(creds.password);
  }
  await expect(email)
    .toHaveValue(creds.email, { timeout: 5_000 })
    .catch(() => {});
  // Botón de ENVÍO exacto: "Sign in" (no el SSO "Sign in with Microsoft", que
  // aparece tras cargar los proveedores y haría match con un selector laxo).
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  // Salimos de /login (resolveAndRoute empuja a /admin/* o /select-tenant).
  await page
    .waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 30_000 })
    .catch(() => {});

  if (page.url().includes("/select-tenant") && creds.tenant) {
    await page
      .getByText(creds.tenant, { exact: false })
      .first()
      .click()
      .catch(() => {});
    await page
      .waitForURL((u) => u.pathname.startsWith("/admin"), { timeout: 30_000 })
      .catch(() => {});
  }
}

/** Guarda el storageState para reutilizar la sesión entre manuales (más rápido). */
export async function saveSession(page: Page, file: string): Promise<void> {
  await page.context().storageState({ path: file });
}
