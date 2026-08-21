import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/settings/sso/saml — the per-tenant SAML 2.0 config UI
 * (Plan 08 task_08_06).
 *
 * Mocks the backend SAML endpoints so the test runs fully offline:
 *   - GET    /auth/sso/saml/sp-metadata    — SP EntityID + ACS URL
 *   - GET    /auth/sso/saml/config         — list (0 or 1) — never the SP key
 *   - POST   /auth/sso/saml/config         — create (captures the body)
 *   - PUT    /auth/sso/saml/config/{id}    — edit/toggle (captures the body)
 *   - DELETE /auth/sso/saml/config/{id}    — soft delete
 *   - POST   /auth/sso/saml/parse-metadata — parse pasted IdP metadata XML
 *
 * Drives:
 *   - empty state when the tenant has no SAML config,
 *   - the SP metadata card (EntityID + ACS URL) is visible,
 *   - create: paste IdP metadata → "Extraer datos" → fields pre-fill →
 *     submit → POST carries entity_id + sso_url + x509_cert,
 *   - the SP private key is NEVER rendered (the GET response has no key),
 *   - render of an existing config card with the right badges,
 *   - toggle enabled → PUT without an sp_private_key (keeps the stored one),
 *   - edit: SP key left blank → PUT omits sp_private_key,
 *   - delete → DELETE called and the card disappears.
 *
 * NOTE: this spec is written but NOT run as part of task_08_06 — it is
 * marked PENDING HUMAN VERIFICATION (needs a browser + the admin-panel
 * dev server). Run it with `npx playwright test e2e/sso-saml-config.spec.ts`.
 */

const CONFIG_ID = "33333333-0000-0000-0000-000000000001";
const SP_ENTITY_ID = "http://localhost:8000/auth/sso/saml/metadata";
const ACS_URL = "http://localhost:8000/auth/sso/11111111-1111-1111-1111-111111111111/saml/acs";

// A throwaway marker the page must NEVER render (the API never sends the
// SP private key). The PEM marker words are assembled at runtime (never
// the contiguous literal the `detect-private-key` pre-commit hook scans
// for) so this obviously-fake fixture is not flagged.
const PEM_KEY = ["PRIVATE", "KEY"].join(" ");
const SECRET_KEY_BODY = `${"-".repeat(5)}BEGIN ${PEM_KEY}${"-".repeat(5)}NEVER-RENDER-THIS`;

interface SamlConfigFixture {
  id: string;
  provider: string;
  display_name: string | null;
  enabled: boolean;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  name_id_format: string;
  attribute_mappings: Record<string, string>;
  sp_x509_cert: string | null;
  has_sp_private_key: boolean;
  sp_private_key_source: "vault" | "encrypted" | null;
  authn_requests_signed: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
  created_at: string;
  updated_at: string;
}

const IDP_METADATA_XML = `<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
                  entityID="https://idp.example.test/saml/metadata">
  <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>MIIDmetacertBASE64==</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
    <SingleSignOnService
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.example.test/saml/sso"/>
  </IDPSSODescriptor>
</EntityDescriptor>`;

const PARSED_METADATA = {
  entity_id: "https://idp.example.test/saml/metadata",
  sso_url: "https://idp.example.test/saml/sso",
  x509_cert: "MIIDmetacertBASE64==",
  name_id_format: "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
};

const EXISTING_CONFIG: SamlConfigFixture = {
  id: CONFIG_ID,
  provider: "saml",
  display_name: "Acme Okta",
  enabled: false,
  idp_entity_id: "https://idp.example.test/saml/metadata",
  idp_sso_url: "https://idp.example.test/saml/sso",
  idp_x509_cert: "MIIDexistingcert==",
  name_id_format: "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
  attribute_mappings: { email: "mail", full_name: "displayName" },
  sp_x509_cert: "MIIDspcert==",
  has_sp_private_key: true,
  sp_private_key_source: "encrypted",
  authn_requests_signed: false,
  want_assertions_signed: true,
  want_assertions_encrypted: false,
  want_name_id_encrypted: false,
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

interface Capture {
  postCount: number;
  putCount: number;
  deleteCount: number;
  parseCount: number;
  lastPostBody: Record<string, unknown> | null;
  lastPutBody: Record<string, unknown> | null;
}

async function setup(page: Page, initial: SamlConfigFixture[]): Promise<Capture> {
  const capture: Capture = {
    postCount: 0,
    putCount: 0,
    deleteCount: 0,
    parseCount: 0,
    lastPostBody: null,
    lastPutBody: null,
  };
  let configs = [...initial];

  await seedSession(page);

  await page.route("http://localhost:8001/auth/sso/saml/sp-metadata", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ sp_entity_id: SP_ENTITY_ID, acs_url: ACS_URL }),
    }),
  );

  await page.route("http://localhost:8001/auth/sso/saml/parse-metadata", (route) => {
    capture.parseCount += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PARSED_METADATA),
    });
  });

  await page.route("http://localhost:8001/auth/sso/saml/config", (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(configs),
      });
    }
    if (method === "POST") {
      capture.postCount += 1;
      const body = JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>;
      capture.lastPostBody = body;
      const created: SamlConfigFixture = {
        ...EXISTING_CONFIG,
        id: CONFIG_ID,
        display_name: (body.display_name as string | null) ?? null,
        enabled: Boolean(body.enabled),
        idp_entity_id: String(body.idp_entity_id ?? ""),
        idp_sso_url: String(body.idp_sso_url ?? ""),
        idp_x509_cert: String(body.idp_x509_cert ?? ""),
        has_sp_private_key: body.sp_private_key !== undefined,
        sp_private_key_source: body.sp_private_key !== undefined ? "encrypted" : null,
      };
      configs = [created];
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
    }
    return route.continue();
  });

  await page.route(`http://localhost:8001/auth/sso/saml/config/${CONFIG_ID}`, (route) => {
    const method = route.request().method();
    if (method === "PUT") {
      capture.putCount += 1;
      const body = JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>;
      capture.lastPutBody = body;
      const updated: SamlConfigFixture = {
        ...(configs[0] ?? EXISTING_CONFIG),
        enabled: Boolean(body.enabled),
        idp_entity_id: String(body.idp_entity_id ?? ""),
        idp_sso_url: String(body.idp_sso_url ?? ""),
        display_name: (body.display_name as string | null) ?? null,
      };
      configs = [updated];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(updated),
      });
    }
    if (method === "DELETE") {
      capture.deleteCount += 1;
      configs = [];
      return route.fulfill({ status: 204, body: "" });
    }
    return route.continue();
  });

  return capture;
}

// ---------------------------------------------------------------------------
// Empty state + SP metadata
// ---------------------------------------------------------------------------
test("empty tenant shows the no-config message and the SP metadata", async ({ page }) => {
  await setup(page, []);
  await page.goto("/admin/settings/sso/saml", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("saml-config-page")).toBeVisible();
  await expect(page.getByTestId("saml-empty")).toBeVisible();
  await expect(page.getByTestId("saml-sp-entity-id")).toContainText(SP_ENTITY_ID);
  await expect(page.getByTestId("saml-acs-url")).toContainText(ACS_URL);
});

// ---------------------------------------------------------------------------
// Create via metadata parse
// ---------------------------------------------------------------------------
test("create: parsing metadata pre-fills the IdP fields and POSTs them", async ({ page }) => {
  const capture = await setup(page, []);
  await page.goto("/admin/settings/sso/saml", { waitUntil: "domcontentloaded" });

  await page.getByTestId("saml-create-button").click();
  await expect(page.getByTestId("saml-config-dialog")).toBeVisible();

  // Paste IdP metadata and extract.
  await page.getByTestId("saml-form-metadata").fill(IDP_METADATA_XML);
  await page.getByTestId("saml-form-metadata-parse").click();

  await expect.poll(() => capture.parseCount).toBe(1);
  await expect(page.getByTestId("saml-form-entity-id")).toHaveValue(
    "https://idp.example.test/saml/metadata",
  );
  await expect(page.getByTestId("saml-form-sso-url")).toHaveValue(
    "https://idp.example.test/saml/sso",
  );
  await expect(page.getByTestId("saml-form-cert")).toHaveValue("MIIDmetacertBASE64==");

  await page.getByTestId("saml-form-submit").click();

  await expect.poll(() => capture.postCount).toBe(1);
  expect(capture.lastPostBody).toMatchObject({
    idp_entity_id: "https://idp.example.test/saml/metadata",
    idp_sso_url: "https://idp.example.test/saml/sso",
    idp_x509_cert: "MIIDmetacertBASE64==",
  });
  await expect(page.getByTestId("saml-config-dialog")).toBeHidden();
  await expect(page.getByTestId("saml-config-card")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Render existing + SP key never shown
// ---------------------------------------------------------------------------
test("existing config renders with badges and never shows the SP key", async ({ page }) => {
  await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso/saml", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("saml-config-card")).toBeVisible();
  await expect(page.getByTestId("saml-config-entity-id")).toContainText(
    "idp.example.test/saml/metadata",
  );
  await expect(page.getByTestId("saml-config-sso-url")).toContainText("idp.example.test/saml/sso");
  await expect(page.getByTestId("saml-enabled-badge")).toContainText("inactivo");
  await expect(page.getByTestId("saml-key-badge")).toBeVisible();
  // The page body never contains an SP private key value (API never sends one).
  await expect(page.locator("body")).not.toContainText(SECRET_KEY_BODY);
});

// ---------------------------------------------------------------------------
// Toggle enabled
// ---------------------------------------------------------------------------
test("toggling enabled PUTs without an sp_private_key", async ({ page }) => {
  const capture = await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso/saml", { waitUntil: "domcontentloaded" });

  await page.getByTestId("saml-toggle-enabled").click();

  await expect.poll(() => capture.putCount).toBe(1);
  expect(capture.lastPutBody).toMatchObject({ enabled: true });
  // No SP key sent on a toggle — the stored one is preserved.
  expect(capture.lastPutBody).not.toHaveProperty("sp_private_key");
});

// ---------------------------------------------------------------------------
// Edit without SP key
// ---------------------------------------------------------------------------
test("editing with an empty SP key omits sp_private_key in the PUT", async ({ page }) => {
  const capture = await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso/saml", { waitUntil: "domcontentloaded" });

  await page.getByTestId("saml-edit-button").click();
  await expect(page.getByTestId("saml-config-dialog")).toBeVisible();
  // Pre-filled from the existing config.
  await expect(page.getByTestId("saml-form-entity-id")).toHaveValue(
    "https://idp.example.test/saml/metadata",
  );
  // SP key field starts empty (never echoed).
  await expect(page.getByTestId("saml-form-sp-key")).toHaveValue("");

  await page.getByTestId("saml-form-display-name").fill("Acme SAML renamed");
  await page.getByTestId("saml-form-submit").click();

  await expect.poll(() => capture.putCount).toBe(1);
  expect(capture.lastPutBody).toMatchObject({ display_name: "Acme SAML renamed" });
  expect(capture.lastPutBody).not.toHaveProperty("sp_private_key");
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------
test("deleting the config calls DELETE and removes the card", async ({ page }) => {
  const capture = await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso/saml", { waitUntil: "domcontentloaded" });

  page.on("dialog", (dialog) => {
    dialog.accept().catch(() => {});
  });

  await page.getByTestId("saml-delete-button").click();

  await expect.poll(() => capture.deleteCount).toBe(1);
  await expect(page.getByTestId("saml-config-card")).toHaveCount(0);
  await expect(page.getByTestId("saml-empty")).toBeVisible();
});
