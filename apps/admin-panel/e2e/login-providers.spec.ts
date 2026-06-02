import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the branded SSO provider buttons on `/login`
 * (ADR 0047, task_sso_05).
 *
 * Auth providers are PLATFORM-GLOBAL: the login page hits the PUBLIC
 * `GET /auth/sso/providers` (no auth, no tenant, NO secrets — only
 * id / kind / display_name / button_label / login_url) and renders one
 * brand button per enabled provider ALONGSIDE the password form. There is
 * no tenant in the URL. Clicking a button does a full-page navigation to
 * the provider's `login_url` on the api-server, which redirects to the IdP.
 *
 * Mocks the providers endpoint so the test runs fully offline. Drives:
 *   - the password form is ALWAYS present (SSO is additive, never a gate),
 *   - no providers → no buttons, no "or with email" divider,
 *   - enabled providers → one branded button each, the operator
 *     `button_label` wins, the brand is inferred from the kind/label
 *     (microsoft / google / github / generic OIDC|SAML),
 *   - the public response carries NO secret (the page body never shows one),
 *   - clicking a button navigates to the provider's `login_url`.
 *
 * NOTE: written but NOT run as part of task_sso_05 (needs a browser + the
 * admin-panel dev server). Run with
 * `npx playwright test e2e/login-providers.spec.ts`.
 */

interface PublicProviderFixture {
  id: string;
  kind: "oidc" | "saml";
  display_name: string | null;
  button_label: string | null;
  login_url: string;
}

const MICROSOFT: PublicProviderFixture = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  kind: "oidc",
  display_name: "Acme Entra ID",
  button_label: "Iniciar sesión con Microsoft",
  login_url: "/auth/sso/aaaaaaaa-0000-0000-0000-000000000001/oidc/login",
};

const GOOGLE: PublicProviderFixture = {
  id: "bbbbbbbb-0000-0000-0000-000000000002",
  kind: "oidc",
  display_name: "Google Workspace",
  button_label: null, // → brand default "Sign in with Google"
  login_url: "/auth/sso/bbbbbbbb-0000-0000-0000-000000000002/oidc/login",
};

const GITHUB: PublicProviderFixture = {
  id: "cccccccc-0000-0000-0000-000000000003",
  kind: "oidc",
  display_name: "GitHub",
  button_label: "Continuar con GitHub",
  login_url: "/auth/sso/cccccccc-0000-0000-0000-000000000003/oidc/login",
};

const GENERIC_SAML: PublicProviderFixture = {
  id: "dddddddd-0000-0000-0000-000000000004",
  kind: "saml",
  display_name: "Corporate IdP",
  button_label: "Acceder con SSO corporativo",
  login_url: "/auth/sso/dddddddd-0000-0000-0000-000000000004/saml/login",
};

/** Mock the PUBLIC providers endpoint with `providers`. */
async function setupProviders(page: Page, providers: PublicProviderFixture[]): Promise<void> {
  await page.route("http://localhost:8001/auth/sso/providers", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(providers),
    }),
  );
}

// ---------------------------------------------------------------------------
// Password form always present
// ---------------------------------------------------------------------------
test("password form is present even with no SSO providers", async ({ page }) => {
  await setupProviders(page, []);
  await page.goto("/login", { waitUntil: "domcontentloaded" });

  // Password login stays intact: email + password + Sign in button.
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();

  // No providers → no buttons block and no "or with email" divider.
  await expect(page.getByTestId("login-providers")).toHaveCount(0);
  await expect(page.getByTestId("login-divider")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// Branded buttons + label/brand resolution
// ---------------------------------------------------------------------------
test("renders one branded button per enabled provider + the divider", async ({ page }) => {
  await setupProviders(page, [MICROSOFT, GOOGLE, GITHUB, GENERIC_SAML]);
  await page.goto("/login", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("login-providers")).toBeVisible();
  // Divider appears only when at least one provider rendered.
  await expect(page.getByTestId("login-divider")).toBeVisible();

  // Password form coexists below the buttons.
  await expect(page.getByLabel("Password")).toBeVisible();

  const ms = page.getByTestId(`login-provider-${MICROSOFT.id}`);
  const google = page.getByTestId(`login-provider-${GOOGLE.id}`);
  const gh = page.getByTestId(`login-provider-${GITHUB.id}`);
  const saml = page.getByTestId(`login-provider-${GENERIC_SAML.id}`);

  await expect(ms).toBeVisible();
  await expect(google).toBeVisible();
  await expect(gh).toBeVisible();
  await expect(saml).toBeVisible();

  // The operator button_label wins when set.
  await expect(ms).toContainText("Iniciar sesión con Microsoft");
  await expect(gh).toContainText("Continuar con GitHub");
  await expect(saml).toContainText("Acceder con SSO corporativo");
  // No label → the brand default text.
  await expect(google).toContainText("Sign in with Google");

  // Brand inferred from kind + label/display_name.
  await expect(ms).toHaveAttribute("data-brand", "microsoft");
  await expect(google).toHaveAttribute("data-brand", "google");
  await expect(gh).toHaveAttribute("data-brand", "github");
  await expect(saml).toHaveAttribute("data-brand", "saml");
});

// ---------------------------------------------------------------------------
// No secrets ever rendered
// ---------------------------------------------------------------------------
test("the public providers list never carries a secret", async ({ page }) => {
  // Even if a (hypothetical) secret leaked into the payload, the UI only
  // reads id/kind/display_name/button_label/login_url — but the real
  // contract is the endpoint NEVER sends one. Assert the body is clean.
  await setupProviders(page, [MICROSOFT, GOOGLE]);
  await page.goto("/login", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("login-providers")).toBeVisible();
  const body = page.locator("body");
  await expect(body).not.toContainText("client_secret");
  await expect(body).not.toContainText("sp_private_key");
});

// ---------------------------------------------------------------------------
// Clicking a button starts the flow (navigates to the api-server login URL)
// ---------------------------------------------------------------------------
test("clicking a provider navigates to its login_url on the api-server", async ({ page }) => {
  await setupProviders(page, [MICROSOFT]);

  // Intercept the login route so the test stays offline; capture the URL
  // the browser tried to navigate to and stop the redirect.
  let navigatedTo: string | null = null;
  await page.route(`http://localhost:8001${MICROSOFT.login_url}`, (route) => {
    navigatedTo = route.request().url();
    // Fulfil with a trivial page so we don't actually leave for an IdP.
    return route.fulfill({ status: 200, contentType: "text/html", body: "<html></html>" });
  });

  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`login-provider-${MICROSOFT.id}`).click();

  await expect
    .poll(() => navigatedTo, { timeout: 5_000 })
    .toBe(`http://localhost:8001${MICROSOFT.login_url}`);
});
