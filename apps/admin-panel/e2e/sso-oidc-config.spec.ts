import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/settings/sso — the per-tenant OIDC config UI
 * (Plan 08 task_08_03).
 *
 * Mocks the backend SSO endpoints so the test runs fully offline:
 *   - GET    /auth/sso/oidc/callback-url — the redirect URL to register
 *   - GET    /auth/sso/oidc/templates    — the per-IdP template picker
 *   - GET    /auth/sso/config            — list (0 or 1) — never the secret
 *   - POST   /auth/sso/config            — create (captures the body)
 *   - PUT    /auth/sso/config/{id}       — edit/toggle (captures the body)
 *   - DELETE /auth/sso/config/{id}       — soft delete
 *
 * Drives:
 *   - empty state when the tenant has no OIDC config,
 *   - the callback URL card is visible,
 *   - create: pick a template → params/issuer pre-fill → fill secret →
 *     submit → POST carries issuer + client_id + client_secret,
 *   - the secret is NEVER rendered (the GET response has no secret field),
 *   - render of an existing config card with the right badges,
 *   - toggle enabled → PUT without a secret (keeps the stored one),
 *   - edit: secret left blank → PUT omits client_secret,
 *   - delete → DELETE called and the card disappears.
 *
 * NOTE: this spec is written but NOT run as part of task_08_03 — it is
 * marked PENDING HUMAN VERIFICATION (needs a browser + the admin-panel
 * dev server). Run it with `npx playwright test e2e/sso-oidc-config.spec.ts`.
 */

const CONFIG_ID = "22222222-0000-0000-0000-000000000001";
const CALLBACK_URL = "http://localhost:8000/auth/sso/oidc/callback";

interface SsoConfigFixture {
  id: string;
  provider: string;
  display_name: string | null;
  enabled: boolean;
  issuer: string;
  client_id: string;
  scopes: string[];
  claim_mappings: Record<string, string>;
  has_client_secret: boolean;
  client_secret_source: "vault" | "encrypted" | null;
  created_at: string;
  updated_at: string;
}

const TEMPLATES = [
  {
    template_id: "azure_ad",
    display_name: "Microsoft Entra ID (Azure AD)",
    issuer_template: "https://login.microsoftonline.com/{tenant}/v2.0",
    default_scopes: ["openid", "email", "profile"],
    claim_mappings: { email: "email", full_name: "name", groups: "groups" },
    required_params: ["tenant"],
    notes: "`tenant` is the Entra directory (tenant) GUID.",
  },
  {
    template_id: "google_workspace",
    display_name: "Google Workspace",
    issuer_template: "https://accounts.google.com",
    default_scopes: ["openid", "email", "profile"],
    claim_mappings: { email: "email", full_name: "name" },
    required_params: [],
    notes: null,
  },
];

const EXISTING_CONFIG: SsoConfigFixture = {
  id: CONFIG_ID,
  provider: "oidc",
  display_name: "Acme Entra ID",
  enabled: false,
  issuer: "https://login.microsoftonline.com/acme-guid/v2.0",
  client_id: "acme-client-id",
  scopes: ["openid", "email", "profile"],
  claim_mappings: { email: "email", full_name: "name" },
  has_client_secret: true,
  client_secret_source: "encrypted",
  created_at: "2026-05-30T00:00:00Z",
  updated_at: "2026-05-30T00:00:00Z",
};

interface Capture {
  postCount: number;
  putCount: number;
  deleteCount: number;
  lastPostBody: Record<string, unknown> | null;
  lastPutBody: Record<string, unknown> | null;
}

async function setup(page: Page, initial: SsoConfigFixture[]): Promise<Capture> {
  const capture: Capture = {
    postCount: 0,
    putCount: 0,
    deleteCount: 0,
    lastPostBody: null,
    lastPutBody: null,
  };
  let configs = [...initial];

  await seedSession(page);

  await page.route("http://localhost:8001/auth/sso/oidc/callback-url", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ callback_url: CALLBACK_URL }),
    }),
  );

  await page.route("http://localhost:8001/auth/sso/oidc/templates", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TEMPLATES),
    }),
  );

  await page.route("http://localhost:8001/auth/sso/config", (route) => {
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
      const created: SsoConfigFixture = {
        ...EXISTING_CONFIG,
        id: CONFIG_ID,
        display_name: (body.display_name as string | null) ?? null,
        enabled: Boolean(body.enabled),
        issuer: String(body.issuer ?? ""),
        client_id: String(body.client_id ?? ""),
        scopes: (body.scopes as string[]) ?? [],
        has_client_secret: body.client_secret !== undefined,
        client_secret_source: body.client_secret !== undefined ? "encrypted" : null,
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

  await page.route(`http://localhost:8001/auth/sso/config/${CONFIG_ID}`, (route) => {
    const method = route.request().method();
    if (method === "PUT") {
      capture.putCount += 1;
      const body = JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>;
      capture.lastPutBody = body;
      const updated: SsoConfigFixture = {
        ...(configs[0] ?? EXISTING_CONFIG),
        enabled: Boolean(body.enabled),
        issuer: String(body.issuer ?? ""),
        client_id: String(body.client_id ?? ""),
        display_name: (body.display_name as string | null) ?? null,
        scopes: (body.scopes as string[]) ?? [],
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
// Empty state + callback URL
// ---------------------------------------------------------------------------
test("empty tenant shows the no-config message and the callback URL", async ({ page }) => {
  await setup(page, []);
  await page.goto("/admin/settings/sso", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("sso-config-page")).toBeVisible();
  await expect(page.getByTestId("sso-empty")).toBeVisible();
  await expect(page.getByTestId("sso-callback-url")).toContainText(CALLBACK_URL);
});

// ---------------------------------------------------------------------------
// Create via template
// ---------------------------------------------------------------------------
test("create: picking a template pre-fills issuer and POSTs the secret", async ({ page }) => {
  const capture = await setup(page, []);
  await page.goto("/admin/settings/sso", { waitUntil: "domcontentloaded" });

  await page.getByTestId("sso-create-button").click();
  await expect(page.getByTestId("sso-config-dialog")).toBeVisible();

  // Pick Azure AD — it has a required `tenant` param.
  await page.getByTestId("sso-form-template").selectOption("azure_ad");
  await expect(page.getByTestId("sso-form-param-tenant")).toBeVisible();
  await page.getByTestId("sso-form-param-tenant").fill("acme-guid");

  // Issuer is rendered from the template + param.
  await expect(page.getByTestId("sso-form-issuer")).toHaveValue(
    "https://login.microsoftonline.com/acme-guid/v2.0",
  );

  await page.getByTestId("sso-form-client-id").fill("acme-client-id");
  await page.getByTestId("sso-form-client-secret").fill("super-secret");
  await page.getByTestId("sso-form-submit").click();

  await expect.poll(() => capture.postCount).toBe(1);
  expect(capture.lastPostBody).toMatchObject({
    issuer: "https://login.microsoftonline.com/acme-guid/v2.0",
    client_id: "acme-client-id",
    client_secret: "super-secret",
  });
  await expect(page.getByTestId("sso-config-dialog")).toBeHidden();
  // The created config now renders as a card.
  await expect(page.getByTestId("sso-config-card")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Render existing + secret never shown
// ---------------------------------------------------------------------------
test("existing config renders with badges and never shows the secret", async ({ page }) => {
  await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("sso-config-card")).toBeVisible();
  await expect(page.getByTestId("sso-config-issuer")).toContainText(
    "login.microsoftonline.com/acme-guid",
  );
  await expect(page.getByTestId("sso-config-client-id")).toContainText("acme-client-id");
  await expect(page.getByTestId("sso-enabled-badge")).toContainText("inactivo");
  await expect(page.getByTestId("sso-secret-badge")).toBeVisible();
  // The page body never contains a secret value (the API never sends one).
  await expect(page.locator("body")).not.toContainText("super-secret");
});

// ---------------------------------------------------------------------------
// Toggle enabled
// ---------------------------------------------------------------------------
test("toggling enabled PUTs without a client_secret", async ({ page }) => {
  const capture = await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso", { waitUntil: "domcontentloaded" });

  await page.getByTestId("sso-toggle-enabled").click();

  await expect.poll(() => capture.putCount).toBe(1);
  expect(capture.lastPutBody).toMatchObject({ enabled: true });
  // No secret sent on a toggle — the stored one is preserved.
  expect(capture.lastPutBody).not.toHaveProperty("client_secret");
});

// ---------------------------------------------------------------------------
// Edit without secret
// ---------------------------------------------------------------------------
test("editing with an empty secret omits client_secret in the PUT", async ({ page }) => {
  const capture = await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso", { waitUntil: "domcontentloaded" });

  await page.getByTestId("sso-edit-button").click();
  await expect(page.getByTestId("sso-config-dialog")).toBeVisible();
  // Pre-filled from the existing config.
  await expect(page.getByTestId("sso-form-client-id")).toHaveValue("acme-client-id");
  // Secret field starts empty (never echoed).
  await expect(page.getByTestId("sso-form-client-secret")).toHaveValue("");

  await page.getByTestId("sso-form-display-name").fill("Acme SSO renamed");
  await page.getByTestId("sso-form-submit").click();

  await expect.poll(() => capture.putCount).toBe(1);
  expect(capture.lastPutBody).toMatchObject({ display_name: "Acme SSO renamed" });
  expect(capture.lastPutBody).not.toHaveProperty("client_secret");
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------
test("deleting the config calls DELETE and removes the card", async ({ page }) => {
  const capture = await setup(page, [EXISTING_CONFIG]);
  await page.goto("/admin/settings/sso", { waitUntil: "domcontentloaded" });

  page.on("dialog", (dialog) => {
    dialog.accept().catch(() => {});
  });

  await page.getByTestId("sso-delete-button").click();

  await expect.poll(() => capture.deleteCount).toBe(1);
  await expect(page.getByTestId("sso-config-card")).toHaveCount(0);
  await expect(page.getByTestId("sso-empty")).toBeVisible();
});
