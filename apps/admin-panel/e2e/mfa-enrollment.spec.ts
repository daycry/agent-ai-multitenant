import { expect, test } from "@playwright/test";

/**
 * MFA TOTP (tanda 2026-07-19) — e2e del ENROLAMIENTO contra la app real.
 *
 * Recorre login → Seguridad → activar: el backend del Plan 08 responde con
 * el secret + otpauth:// URI y la UI debe mostrar el QR, la clave manual y
 * los códigos de recuperación. NO se confirma el factor (se abandona sin
 * introducir código): un enrolamiento sin confirmar no gatea el login, así
 * que el tenant de e2e queda igual que estaba y el resto de specs no se ven
 * afectados. El desafío del login con factor confirmado queda cubierto por
 * los tests de componente (vitest) — activarlo aquí exigiría generar códigos
 * TOTP reales y dejaría el usuario compartido gateado.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

test("el enrolamiento TOTP muestra QR, clave manual y códigos de recuperación", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  // `exact`: en dev conviven el submit "Sign in" y los botones SSO
  // "Sign in with …" (multi-provider) — el regex laxo resuelve a ambos.
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);

  await page.goto("/admin/settings/security");
  // Estado inicial esperado del usuario e2e: sin factor confirmado.
  await expect(page.getByTestId("mfa-status-off")).toBeVisible();

  await page.getByTestId("mfa-enroll-button").click();

  // El QR (SVG de qrcode.react), la clave y los códigos de un solo vistazo.
  await expect(page.getByTestId("mfa-qr")).toBeVisible();
  await expect(page.getByTestId("mfa-qr").locator("svg")).toBeVisible();
  await expect(page.getByTestId("mfa-recovery")).toBeVisible();
  await expect(page.getByTestId("mfa-confirm-input")).toBeVisible();

  // Abandonar SIN confirmar: al volver, el factor sigue sin activar (un
  // enrolamiento no confirmado no gatea el login del usuario compartido).
  await page.goto("/admin/settings/security");
  await expect(page.getByTestId("mfa-status-off")).toBeVisible();
});
