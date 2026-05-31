import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * E2E for /admin/llm-providers — the System-Admin 'Proveedores LLM'
 * screen (Plan 11.2 task_11_2_05, ADR 0028).
 *
 * LLM providers (the four closed ADR-0021 paths: claude_sdk / copilot /
 * azure_foundry / ollama) are **platform-global** and managed ONLY by
 * the System Admin — no tenant_id, no RLS; every endpoint gates on
 * require_system_admin on the BYPASSRLS admin session. This screen lets a
 * System Admin list providers (kind + display_name + is_active +
 * has_credential), create / edit with credential fields that switch by
 * kind, toggle active, run "probar conexión", and drive the GitHub
 * Copilot Device Flow.
 *
 * SECRETS ONLY TO VAULT (CLAUDE.md / ADR 0028): the API never returns the
 * credential value — only the has_credential boolean. The credential
 * inputs are write-only; on edit they show "configured", not the value.
 * These specs assert the create/edit requests carry the credential field
 * (which the backend writes to Vault) and that nothing echoes the secret
 * back into the UI.
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET    /me                                  — a SYSTEM ADMIN
 *   - GET    /admin/llm-providers                 — provider list
 *   - POST   /admin/llm-providers                 — create (201)
 *   - PUT    /admin/llm-providers/{id}            — update / toggle (200)
 *   - DELETE /admin/llm-providers/{id}            — delete (204)
 *   - POST   /admin/llm-providers/{id}/test       — liveness probe
 *   - POST   /admin/llm/copilot/device-flow/start — device flow start
 *   - POST   /admin/llm/copilot/device-flow/poll  — device flow poll
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_11_2_05 — it is
 * marked PENDING HUMAN VERIFICATION (needs a browser + the admin-panel
 * dev server). Run with `npx playwright test e2e/llm-providers.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const OLLAMA_ID = "aaaa1111-0000-0000-0000-0000000000a1";
const COPILOT_ID = "aaaa2222-0000-0000-0000-0000000000a2";

const SYSTEM_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "sysadmin@platform.test",
  full_name: "System Admin",
  is_system_admin: true,
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

const OLLAMA_PROVIDER = {
  id: OLLAMA_ID,
  kind: "ollama",
  display_name: "Ollama local",
  base_url: "http://localhost:11434",
  is_active: true,
  config: {},
  secret_vault_path: null,
  has_credential: false,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

const COPILOT_PROVIDER = {
  id: COPILOT_ID,
  kind: "copilot",
  display_name: "Copilot empresa",
  base_url: null,
  is_active: false,
  config: {},
  secret_vault_path: `platform/llm/${COPILOT_ID}`,
  has_credential: true,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

/** Route /me + the provider list. Per-test write/test routes added by callers. */
async function setup(
  page: Page,
  listRows: unknown[] = [OLLAMA_PROVIDER, COPILOT_PROVIDER],
): Promise<void> {
  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(SYSTEM_ADMIN),
    }),
  );

  await page.route("http://localhost:8001/admin/llm-providers", (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(listRows),
    });
  });
}

// ---------------------------------------------------------------------------
// List — kind + credential + state
// ---------------------------------------------------------------------------
test("lists providers with kind, credential and active state", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("llm-providers-page")).toBeVisible();
  await expect(page.getByTestId("providers-table")).toBeVisible();
  await expect(page.getByTestId(`provider-row-${OLLAMA_ID}`)).toBeVisible();
  await expect(page.getByTestId(`provider-row-${COPILOT_ID}`)).toBeVisible();

  // has_credential renders "configurada" vs "sin credencial" — never a value.
  await expect(page.getByTestId(`provider-credential-${OLLAMA_ID}`)).toContainText(
    "sin credencial",
  );
  await expect(page.getByTestId(`provider-credential-${COPILOT_ID}`)).toContainText("configurada");
});

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------
test("shows an empty state when there are no providers", async ({ page }) => {
  await setup(page, []);
  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("providers-empty")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Create — credential fields switch by kind; the secret rides in the POST
// ---------------------------------------------------------------------------
test("creates an azure_foundry provider; api_key + base_url ride in the POST", async ({ page }) => {
  await setup(page);

  let posted: Record<string, unknown> = {};
  await page.route("http://localhost:8001/admin/llm-providers", (route: Route) => {
    if (route.request().method() === "POST") {
      posted = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          ...OLLAMA_PROVIDER,
          id: "cccc3333-0000-0000-0000-0000000000c3",
          kind: "azure_foundry",
        }),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId("provider-create-open").click();
  await expect(page.getByTestId("provider-form-dialog")).toBeVisible();

  await page.getByTestId("form-kind").selectOption("azure_foundry");
  await page.getByTestId("form-display-name").fill("Azure prod");
  await page.getByTestId("form-base-url").fill("https://apim.example.com/openai");
  // The credential field is the API key for azure_foundry.
  await expect(page.getByTestId("form-api-key")).toBeVisible();
  await page.getByTestId("form-api-key").fill("super-secret-apim-key");
  await page.getByTestId("provider-form-submit").click();

  await expect.poll(() => posted.kind).toBe("azure_foundry");
  expect(posted.display_name).toBe("Azure prod");
  expect(posted.base_url).toBe("https://apim.example.com/openai");
  // The secret rides in the POST so the backend can write it to Vault.
  expect(posted.api_key).toBe("super-secret-apim-key");
});

test("ollama credential input is the optional bearer token", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId("provider-create-open").click();

  await page.getByTestId("form-kind").selectOption("ollama");
  await expect(page.getByTestId("form-bearer-token")).toBeVisible();
  // base_url is required for ollama.
  await expect(page.getByTestId("form-base-url")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Edit — kind immutable; blank secret keeps the existing Vault credential
// ---------------------------------------------------------------------------
test("edit keeps kind immutable and shows 'configured' for the secret", async ({ page }) => {
  await setup(page);

  let put: Record<string, unknown> = {};
  await page.route(`http://localhost:8001/admin/llm-providers/${COPILOT_ID}`, (route: Route) => {
    if (route.request().method() === "PUT") {
      put = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...COPILOT_PROVIDER, display_name: "Copilot renombrado" }),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`provider-edit-${COPILOT_ID}`).click();
  await expect(page.getByTestId("provider-form-dialog")).toBeVisible();

  // kind is immutable on edit.
  await expect(page.getByTestId("form-kind")).toBeDisabled();
  // The write-only credential input shows a "configured" placeholder, NOT a value.
  await expect(page.getByTestId("form-oauth-token")).toHaveValue("");

  await page.getByTestId("form-display-name").fill("Copilot renombrado");
  // Leave the secret blank → keep the existing Vault credential.
  await page.getByTestId("provider-form-submit").click();

  await expect.poll(() => put.display_name).toBe("Copilot renombrado");
  // A blank secret must NOT be sent (keep the current Vault credential).
  expect(put).not.toHaveProperty("oauth_token");
  // kind is never sent on a PUT.
  expect(put).not.toHaveProperty("kind");
});

// ---------------------------------------------------------------------------
// Active toggle (PUT is_active) without opening the dialog
// ---------------------------------------------------------------------------
test("toggles a provider active state", async ({ page }) => {
  await setup(page);

  let put: Record<string, unknown> = {};
  await page.route(`http://localhost:8001/admin/llm-providers/${OLLAMA_ID}`, (route: Route) => {
    if (route.request().method() === "PUT") {
      put = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...OLLAMA_PROVIDER, is_active: false }),
      });
    }
    return route.fallback();
  });

  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`provider-toggle-${OLLAMA_ID}`).click();

  // OLLAMA is active → toggling sends is_active:false.
  await expect.poll(() => put.is_active).toBe(false);
});

// ---------------------------------------------------------------------------
// Probar conexión — ok + error classified outcomes
// ---------------------------------------------------------------------------
test("probar conexión shows an OK result", async ({ page }) => {
  await setup(page);
  await page.route(
    `http://localhost:8001/admin/llm-providers/${OLLAMA_ID}/test`,
    (route: Route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, status: "ok", detail: "reachable" }),
        });
      }
      return route.fallback();
    },
  );

  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`provider-test-${OLLAMA_ID}`).click();

  const result = page.getByTestId(`provider-test-result-${OLLAMA_ID}`);
  await expect(result).toBeVisible();
  await expect(result).toHaveAttribute("data-ok", "true");
  await expect(result).toContainText("conexión OK");
});

test("probar conexión shows a classified error", async ({ page }) => {
  await setup(page);
  await page.route(
    `http://localhost:8001/admin/llm-providers/${OLLAMA_ID}/test`,
    (route: Route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: false,
            status: "connection_error",
            detail: "connection refused",
          }),
        });
      }
      return route.fallback();
    },
  );

  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`provider-test-${OLLAMA_ID}`).click();

  const result = page.getByTestId(`provider-test-result-${OLLAMA_ID}`);
  await expect(result).toBeVisible();
  await expect(result).toHaveAttribute("data-ok", "false");
  await expect(result).toContainText("error de conexión");
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------
test("deletes a provider", async ({ page }) => {
  await setup(page);

  let deletedPath: string | null = null;
  await page.route(`http://localhost:8001/admin/llm-providers/${OLLAMA_ID}`, (route: Route) => {
    if (route.request().method() === "DELETE") {
      deletedPath = new URL(route.request().url()).pathname;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });

  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`provider-delete-${OLLAMA_ID}`).click();

  await expect.poll(() => deletedPath).toBe(`/admin/llm-providers/${OLLAMA_ID}`);
});

// ---------------------------------------------------------------------------
// Copilot Device Flow — start shows user_code + verification_uri; poll
// reaches authorized; the token never appears in the UI.
// ---------------------------------------------------------------------------
test("copilot device flow: start shows code + link, poll authorizes", async ({ page }) => {
  await setup(page);

  await page.route("http://localhost:8001/admin/llm/copilot/device-flow/start", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        provider_id: COPILOT_ID,
        device_code: "dev-code-xyz",
        user_code: "WXYZ-1234",
        verification_uri: "https://github.com/login/device",
        expires_in: 900,
        interval: 1,
      }),
    }),
  );

  let polls = 0;
  await page.route("http://localhost:8001/admin/llm/copilot/device-flow/poll", (route: Route) => {
    polls += 1;
    const authorized = polls >= 2;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: authorized ? "authorized" : "pending",
        authorized,
        interval: authorized ? null : 1,
      }),
    });
  });

  await page.goto("/admin/llm-providers", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`provider-device-flow-${COPILOT_ID}`).click();
  await expect(page.getByTestId("device-flow-dialog")).toBeVisible();

  await page.getByTestId("device-flow-start").click();

  // The operator-facing code + verification link must appear.
  await expect(page.getByTestId("device-flow-user-code")).toHaveText("WXYZ-1234");
  await expect(page.getByTestId("device-flow-verification-link")).toHaveAttribute(
    "href",
    "https://github.com/login/device",
  );

  // Polling completes → authorized banner. The token is in Vault, never in the UI.
  await expect(page.getByTestId("device-flow-authorized")).toBeVisible({ timeout: 10000 });
  const body = await page.getByTestId("device-flow-dialog").textContent();
  expect(body ?? "").not.toContain("dev-code-xyz");
});
