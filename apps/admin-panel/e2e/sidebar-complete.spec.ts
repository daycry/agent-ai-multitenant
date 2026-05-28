import { expect, test } from "@playwright/test";

/**
 * E2E for the global sidebar (Plan 06.6 task_06_6_10).
 *
 * Verifies that the 10 expected entries are present.
 */

const EXPECTED_LABELS = [
  "Dashboard",
  "Agentes",
  "Equipos",
  "Proyectos",
  "Tablero",
  "Aprobaciones",
  "Validación humana",
  "Memorias",
  "Documentos",
  "Settings",
];

test("sidebar contains the 10 nav entries", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  // Mock /me so admin-shell renders without redirecting to login.
  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ user_id: "u", tenant_id: "t", role: "admin" }),
    }),
  );
  await page.goto("/admin/dashboard", { waitUntil: "domcontentloaded" });

  for (const label of EXPECTED_LABELS) {
    await expect(page.getByRole("link", { name: label }).first()).toBeVisible();
  }
});
