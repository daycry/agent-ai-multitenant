import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the top-header tenant picker.
 *
 * Wires together the three backend pieces:
 *   1. First-user auto-promotion to system_admin (the test admin
 *      seeded by run-e2e.ps1 is the first user → superadmin).
 *   2. /admin/tenants visible to superadmins.
 *   3. apiFetch injecting X-Tenant-Id from localStorage.
 *
 * Coverage:
 *   - Picker visible for superadmin (default state: "Todos los
 *     tenants" because no tenant is selected yet).
 *   - The platform tenant (00000000-0000-0000-0000-000000000001) is
 *     hidden from the list — it's reserved for built-in catalogs.
 *   - Selecting a (mocked) tenant updates the label, persists in
 *     localStorage, and the next apiFetch call sends X-Tenant-Id.
 *   - "Todos los tenants" clears the selection again.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);
}

test("picker shows for superadmin and defaults to 'Todos los tenants'", async ({ page }) => {
  await login(page);
  await expect(page.getByTestId("tenant-picker")).toBeVisible();
  await expect(page.getByTestId("tenant-picker-label")).toHaveText("Todos los tenants");
});

test("the platform tenant is hidden from the picker options", async ({ page }) => {
  // Mock /admin/tenants to return the platform tenant plus a real one.
  // The picker must filter the platform UUID out.
  await page.route("**/admin/tenants", async (route) => {
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

  await login(page);
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
  await page.route("**/admin/tenants", async (route) => {
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
  await page.route("**/projects", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    lastTenantHeader = route.request().headers()["x-tenant-id"] ?? null;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });

  await login(page);
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
  await page.route("**/admin/tenants", async (route) => {
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

  await login(page);
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
