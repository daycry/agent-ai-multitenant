import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/marketplace — the cohesive Tenant-Admin marketplace
 * management area (Plan 09 task_09_18).
 *
 * Brings together, in one tabbed page, the surfaces from the earlier Plan 09
 * tasks: browse the catalog (links to Playwright config 09_13 + the consent
 * screen 09_07), manage installed items (consent / revoke / uninstall), and
 * manage cross-tenant shares (09_17 — opt-in, explicit grant, System-Admin
 * audited). The private-listings surface (09_16) is reached by a link.
 *
 * Multi-tenancy is the FEATURE here: private listings are RLS-isolated
 * (tenant_id non-null), and a cross-tenant share is an explicit, audited
 * grant — NEVER an implicit RLS bypass. This spec drives the OWNER side
 * (create + revoke a share); the backend enforces the boundary and is
 * covered by tests/integration/test_cross_tenant_sharing.py
 * (@pytest.mark.cross_tenant).
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET    /me                                    — tenant_admin membership
 *   - GET    /marketplace/listings                  — catalog (global + own private)
 *   - GET    /marketplace/installations             — installed items
 *   - POST   /marketplace/installations/{id}/revoke — revoke (200)
 *   - DELETE /marketplace/installations/{id}        — uninstall (204)
 *   - GET    /marketplace/shares                     — owner's grants
 *   - POST   /marketplace/shares                     — create share (201)
 *   - DELETE /marketplace/shares/{id}                — revoke share (204)
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_09_18 — it is marked
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server).
 * Run it with `npx playwright test e2e/marketplace-admin.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const TARGET_TENANT_ID = "99999999-0000-0000-0000-000000000099";
const PRIVATE_ID = "22222222-0000-0000-0000-000000000002";
const GLOBAL_ID = "33333333-0000-0000-0000-000000000003";
const PLAYWRIGHT_ID = "44444444-0000-0000-0000-000000000004";
const INSTALL_ID = "55555555-0000-0000-0000-000000000005";
const SHARE_ID = "66666666-0000-0000-0000-000000000006";
const NEW_SHARE_ID = "77777777-0000-0000-0000-000000000007";

const ME = {
  user_id: "aaaaaaaa-0000-0000-0000-00000000000a",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    {
      tenant_id: TENANT_ID,
      tenant_name: "Tenant A",
      role: "tenant_admin",
      is_active: true,
    },
  ],
  active_tenant_id: TENANT_ID,
};

const PRIVATE_LISTING = {
  id: PRIVATE_ID,
  source_id: "bbbbbbbb-0000-0000-0000-00000000000b",
  tenant_id: TENANT_ID,
  kind: "skill",
  name: "internal-reporter",
  version: "1.0.0",
  description: "Generates the weekly internal status report.",
  author: "Team A",
  trust_level: "community",
  requested_permissions: [{ type: "network_policy", value: "none" }],
  is_signed: false,
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

const GLOBAL_LISTING = {
  id: GLOBAL_ID,
  source_id: "cccccccc-0000-0000-0000-00000000000c",
  tenant_id: null,
  kind: "tool",
  name: "public-tool",
  version: "1.0.0",
  description: "A global catalog tool.",
  author: "Platform",
  trust_level: "verified",
  requested_permissions: [],
  is_signed: true,
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

const PLAYWRIGHT_LISTING = {
  id: PLAYWRIGHT_ID,
  source_id: "dddddddd-0000-0000-0000-00000000000d",
  tenant_id: null,
  kind: "tool",
  name: "playwright",
  version: "1.0.0",
  description: "Browser automation tool.",
  author: "Platform",
  trust_level: "verified",
  requested_permissions: [],
  is_signed: true,
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

const INSTALLATION = {
  id: INSTALL_ID,
  tenant_id: TENANT_ID,
  listing_id: GLOBAL_ID,
  project_id: null,
  version: "1.0.0",
  status: "enabled",
  granted_permissions: [],
  denied_permissions: [],
  installed_by: ME.user_id,
  installed_at: "2026-05-30T00:00:00Z",
  revoked_at: null,
  revoked_by: null,
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

const SHARE = {
  id: SHARE_ID,
  listing_id: PRIVATE_ID,
  owner_tenant_id: TENANT_ID,
  target_tenant_id: TARGET_TENANT_ID,
  granted_by: ME.user_id,
  revoked_at: null,
  revoked_by: null,
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

async function setup(page: Page): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ME) }),
  );

  await page.route("http://localhost:8001/marketplace/listings**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([PRIVATE_LISTING, GLOBAL_LISTING, PLAYWRIGHT_LISTING]),
    }),
  );

  await page.route("http://localhost:8001/marketplace/installations**", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([INSTALLATION]),
      });
    }
    return route.fallback();
  });

  await page.route("http://localhost:8001/marketplace/shares", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([SHARE]),
      });
    }
    return route.fallback();
  });
}

// ---------------------------------------------------------------------------
// Catalog — browse, y NADA de configuración (task_mkt2_13 / ADR 0142)
// ---------------------------------------------------------------------------
test("catalog tab lists global + private listings, offers install and no install-time config", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("marketplace-admin-page")).toBeVisible();
  await expect(page.getByTestId("catalog-list")).toBeVisible();
  await expect(page.getByTestId(`catalog-listing-${PRIVATE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`catalog-listing-${GLOBAL_ID}`)).toBeVisible();
  // private vs global badges
  await expect(page.getByTestId(`catalog-private-${PRIVATE_ID}`)).toBeVisible();
  await expect(page.getByTestId(`catalog-global-${GLOBAL_ID}`)).toBeVisible();
  // El catálogo ya NO ofrece configurar Playwright: con las tres capas del ADR
  // 0142 la config es del despliegue (por proyecto), no de la instalación. La
  // aserción es en negativo a propósito — es la que se pondría roja si alguien
  // reintrodujera el formulario en el flujo de instalación.
  await expect(page.getByTestId(`catalog-playwright-config-${PLAYWRIGHT_ID}`)).toHaveCount(0);
  // `task_mk_00`: lo que SÍ ofrece ahora es instalar. Hasta 2026-09-03 el panel
  // no emitía un solo POST de instalación: se instalaba llamando a la API.
  await expect(page.getByTestId(`catalog-install-${PRIVATE_ID}`)).toBeVisible();
  // …salvo en lo que ya está instalado (el fixture INSTALLATION apunta al
  // listing global), que se dice en vez de ofrecerse dos veces.
  await expect(page.getByTestId(`catalog-installed-${GLOBAL_ID}`)).toBeVisible();
});

// ---------------------------------------------------------------------------
// Catalog — instalar (`task_mk_00`)
// ---------------------------------------------------------------------------
test("installing from the catalog posts the listing and lands where the flow continues", async ({
  page,
}) => {
  await setup(page);

  // Ruta MÁS específica que la del setup y registrada después: Playwright las
  // resuelve LIFO, así que ésta atiende el POST y el GET sigue cayendo al
  // handler de arriba por `fallback()`.
  const enviados: unknown[] = [];
  await page.route("http://localhost:8001/marketplace/installations", (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    enviados.push(route.request().postDataJSON());
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        ...INSTALLATION,
        id: "inst-nueva",
        listing_id: PRIVATE_ID,
        status: "disabled",
      }),
    });
  });
  await page.route(
    "http://localhost:8001/marketplace/installations/inst-nueva/permissions",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          installation_id: "inst-nueva",
          listing_id: PRIVATE_ID,
          status: "disabled",
          requested_permissions: [],
          granted_permissions: [],
          denied_permissions: [],
        }),
      }),
  );

  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`catalog-install-${PRIVATE_ID}`).click();

  // Manda el listing correcto y NO pide el camino asíncrono, que sin worker en
  // la lane `marketplace` dejaría la instalación en `analyzing` para siempre.
  await expect.poll(() => enviados.length).toBe(1);
  const cuerpo = enviados[0] as { listing_id: string; async_gates?: boolean };
  expect(cuerpo.listing_id).toBe(PRIVATE_ID);
  expect(cuerpo.async_gates ?? false).toBe(false);

  // Nace `disabled` porque su nivel de confianza exige consentimiento: el paso
  // que falta es otorgarlo, y ahí es donde se aterriza.
  await expect
    .poll(() => new URL(page.url()).pathname)
    .toBe("/admin/marketplace/installations/inst-nueva/permissions");
});

// ---------------------------------------------------------------------------
// Installed — consent link + revoke + uninstall
// ---------------------------------------------------------------------------
test("installed tab links consent and revokes / uninstalls", async ({ page }) => {
  await setup(page);

  let revokedPath: string | null = null;
  await page.route(
    `http://localhost:8001/marketplace/installations/${INSTALL_ID}/revoke`,
    (route) => {
      revokedPath = new URL(route.request().url()).pathname;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...INSTALLATION, status: "revoked" }),
      });
    },
  );

  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });
  await page.getByTestId("marketplace-tab-installed").click();

  await expect(page.getByTestId(`installed-${INSTALL_ID}`)).toBeVisible();
  await expect(page.getByTestId(`installed-consent-${INSTALL_ID}`)).toHaveAttribute(
    "href",
    `/admin/marketplace/installations/${INSTALL_ID}/permissions`,
  );

  await page.getByTestId(`installed-revoke-${INSTALL_ID}`).click();
  await expect.poll(() => revokedPath).toBe(`/marketplace/installations/${INSTALL_ID}/revoke`);
});

test("installed tab uninstalls a listing", async ({ page }) => {
  await setup(page);

  let deletedPath: string | null = null;
  await page.route(`http://localhost:8001/marketplace/installations/${INSTALL_ID}`, (route) => {
    if (route.request().method() === "DELETE") {
      deletedPath = new URL(route.request().url()).pathname;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });

  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });
  await page.getByTestId("marketplace-tab-installed").click();

  await page.getByTestId(`installed-uninstall-${INSTALL_ID}`).click();
  await expect.poll(() => deletedPath).toBe(`/marketplace/installations/${INSTALL_ID}`);
});

// ---------------------------------------------------------------------------
// Shares — create a cross-tenant share (opt-in, explicit grant)
// ---------------------------------------------------------------------------
test("shares tab creates an explicit cross-tenant share grant", async ({ page }) => {
  await setup(page);

  let posted: { listing_id?: string; target_tenant_id?: string } = {};
  await page.route("http://localhost:8001/marketplace/shares", async (route) => {
    if (route.request().method() === "POST") {
      posted = route.request().postDataJSON() as {
        listing_id?: string;
        target_tenant_id?: string;
      };
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ ...SHARE, id: NEW_SHARE_ID }),
      });
    }
    // GET
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([SHARE]),
    });
  });

  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });
  await page.getByTestId("marketplace-tab-shares").click();

  await expect(page.getByTestId("share-create-card")).toBeVisible();
  await page.getByTestId("share-listing-select").selectOption(PRIVATE_ID);
  await page.getByTestId("share-target-input").fill(TARGET_TENANT_ID);
  await page.getByTestId("share-submit").click();

  await expect.poll(() => posted.listing_id).toBe(PRIVATE_ID);
  expect(posted.target_tenant_id).toBe(TARGET_TENANT_ID);
});

// ---------------------------------------------------------------------------
// Shares — revoke a grant (removes the target's visibility immediately)
// ---------------------------------------------------------------------------
test("shares tab revokes a grant", async ({ page }) => {
  await setup(page);

  let revokedSharePath: string | null = null;
  await page.route(`http://localhost:8001/marketplace/shares/${SHARE_ID}`, (route) => {
    revokedSharePath = new URL(route.request().url()).pathname;
    return route.fulfill({ status: 204, body: "" });
  });

  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });
  await page.getByTestId("marketplace-tab-shares").click();

  await expect(page.getByTestId(`share-${SHARE_ID}`)).toBeVisible();
  await page.getByTestId(`share-revoke-${SHARE_ID}`).click();

  await expect.poll(() => revokedSharePath).toBe(`/marketplace/shares/${SHARE_ID}`);
});
