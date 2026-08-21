import { expect, test, type Page } from "@playwright/test";

import { apiRoute } from "./helpers/api";
import { seedSession, systemAdminMe } from "./helpers/session";

/**
 * E2E for the top-header tenant picker.
 *
 * Coverage:
 *   - Picker visible for superadmin (default state: "Todos los
 *     tenants" because no tenant is selected yet).
 *   - The platform tenant (00000000-0000-0000-0000-000000000001) is
 *     hidden from the list — it's reserved for built-in catalogs.
 *   - Selecting a tenant updates the label, persists in localStorage,
 *     and the next apiFetch call sends X-Tenant-Id.
 *   - "Todos los tenants" clears the selection again.
 *
 * ---------------------------------------------------------------------------
 * 2026-08-19 — MIGRADO a sesión sembrada (ADR 0133). Hacía LOGIN REAL, y CI lo
 * barre dentro del subset "mockeado" (usa `page.route`), donde no hay
 * api-server: los cuatro tests morían en `toHaveURL(/admin/dashboard)` sin
 * llegar a ver la cabecera. Lo que comprobaban —que el picker filtra el tenant
 * de plataforma, que la elección viaja en `X-Tenant-Id` y sobrevive a una
 * recarga, y que crear un tenant deriva el slug— es comportamiento del PANEL y
 * se sigue comprobando igual; lo único que cambia es de dónde sale la identidad
 * de superadmin, que antes exigía la BD de e2e con su primer usuario sembrado.
 */

/** Prefijo de la clave que persiste abierto/cerrado cada grupo del menú. */
const NAV_GROUP_LS_PREFIX = "agentic.nav.group.";

/** Siembra la sesión de superadmin. Va ANTES de los `page.route` del spec. */
async function seedSuperadmin(page: Page): Promise<void> {
  await seedSession(page, { me: systemAdminMe() });
  // "Proyectos" vive en el grupo "recursos", que arranca PLEGADO fuera de sus
  // rutas: su enlace ni siquiera está en el DOM hasta que se abre.
  await page.addInitScript(
    ([key]) => window.localStorage.setItem(key, "1"),
    [NAV_GROUP_LS_PREFIX + "recursos"],
  );
}

/** Abre el panel ya autenticado y espera a que la cabecera esté montada. */
async function openPanel(page: Page): Promise<void> {
  await page.goto("/admin/dashboard", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("tenant-picker")).toBeVisible();
}

test("picker shows for superadmin and defaults to 'Todos los tenants'", async ({ page }) => {
  await seedSuperadmin(page);
  await openPanel(page);
  await expect(page.getByTestId("tenant-picker-label")).toHaveText("Todos los tenants");
});

test("the platform tenant is hidden from the picker options", async ({ page }) => {
  await seedSuperadmin(page);
  // Mock /admin/tenants to return the platform tenant plus a real one.
  // The picker must filter the platform UUID out.
  await page.route(apiRoute("/admin/tenants"), async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { id: "00000000-0000-0000-0000-000000000001", name: "Platform", slug: "platform" },
        {
          id: "11111111-1111-1111-1111-111111111111",
          name: "Acme Corp",
          slug: "acme",
        },
      ]),
    });
  });

  await openPanel(page);
  await page.getByTestId("tenant-picker").click();
  await expect(page.getByTestId("tenant-picker-popover")).toBeVisible();

  await expect(
    page.getByTestId("tenant-picker-option-11111111-1111-1111-1111-111111111111"),
  ).toBeVisible();
  await expect(
    page.getByTestId("tenant-picker-option-00000000-0000-0000-0000-000000000001"),
  ).toHaveCount(0);
});

test("selecting a tenant injects X-Tenant-Id on subsequent fetches and persists", async ({
  page,
}) => {
  await seedSuperadmin(page);
  await page.route(apiRoute("/admin/tenants"), async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "11111111-1111-1111-1111-111111111111",
          name: "Acme Corp",
          slug: "acme",
        },
      ]),
    });
  });

  // Capture the X-Tenant-Id header on any /projects GET after the
  // tenant is selected.
  let lastTenantHeader: string | null = null;
  await page.route(apiRoute("/projects*"), async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    lastTenantHeader = route.request().headers()["x-tenant-id"] ?? null;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  await openPanel(page);
  await page.getByTestId("tenant-picker").click();
  await page.getByTestId("tenant-picker-option-11111111-1111-1111-1111-111111111111").click();
  await expect(page.getByTestId("tenant-picker-label")).toHaveText("Acme Corp");

  // Navigate to a screen that triggers a /projects fetch.
  await page.getByTestId("nav-projects").click();
  await expect(page).toHaveURL(/\/admin\/projects$/);

  await expect
    .poll(() => lastTenantHeader, { timeout: 5_000 })
    .toBe("11111111-1111-1111-1111-111111111111");

  // Persistence: localStorage carries the choice across a reload.
  const stored = await page.evaluate(() => localStorage.getItem("admin-panel.tenant-id"));
  expect(stored).toBe("11111111-1111-1111-1111-111111111111");

  // Picking "Todos los tenants" clears the selection (no X-Tenant-Id).
  lastTenantHeader = "still-set";
  await page.getByTestId("tenant-picker").click();
  await page.getByTestId("tenant-picker-all").click();
  await expect(page.getByTestId("tenant-picker-label")).toHaveText("Todos los tenants");

  // Trigger another /projects fetch — header should be absent now.
  await page.reload();
  await expect.poll(() => lastTenantHeader, { timeout: 5_000 }).toBeNull();
});

test("creating a tenant from the dialog selects it and auto-derives the slug", async ({ page }) => {
  // Start with an empty tenant list, then have the POST return a
  // fresh tenant and the follow-up GET include it.
  let created = false;
  await seedSuperadmin(page);
  await page.route(apiRoute("/admin/tenants"), async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          created
            ? [
                {
                  id: "22222222-2222-2222-2222-222222222222",
                  name: "Equipo Plataforma",
                  slug: "equipo-plataforma",
                },
              ]
            : [],
        ),
      });
      return;
    }
    if (method === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      // The dialog must auto-derive the slug from the name.
      expect(body).toMatchObject({
        name: "Equipo Plataforma",
        slug: "equipo-plataforma",
      });
      created = true;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "22222222-2222-2222-2222-222222222222",
          name: "Equipo Plataforma",
          slug: "equipo-plataforma",
        }),
      });
      return;
    }
    await route.continue();
  });

  await openPanel(page);
  await page.getByTestId("tenant-picker").click();
  await expect(page.getByTestId("tenant-picker-empty")).toBeVisible();

  await page.getByTestId("tenant-picker-create").click();
  await expect(page.getByTestId("create-tenant-dialog")).toBeVisible();

  // Typing the name auto-fills the slug field.
  await page.getByTestId("create-tenant-name").fill("Equipo Plataforma");
  await expect(page.getByTestId("create-tenant-slug")).toHaveValue("equipo-plataforma");

  await page.getByTestId("create-tenant-submit").click();

  // Dialog closes and the new tenant becomes the active selection.
  await expect(page.getByTestId("create-tenant-dialog")).toHaveCount(0);
  await expect(page.getByTestId("tenant-picker-label")).toHaveText("Equipo Plataforma");
});
