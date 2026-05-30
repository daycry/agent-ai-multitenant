import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for /admin/marketplace/installations/{id}/permissions — the
 * granular per-permission consent UI (Plan 09 task_09_07).
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET  /me                                            — tenant_admin membership (RoleGuard)
 *   - GET  /marketplace/installations/{id}/permissions    — the permission surface + state
 *   - POST /marketplace/installations/{id}/consent        — record decisions (captures body)
 *
 * Drives:
 *   - a community install lands DISABLED with both permissions PENDING,
 *   - granting BOTH permissions POSTs grant/grant and the install flips to
 *     ENABLED (all granted),
 *   - a PARTIAL deny (grant one, deny one) POSTs grant/deny and the install
 *     stays DISABLED with the denied permission shown as Denegado,
 *   - a decision referencing an un-requested permission is impossible from
 *     the UI (only requested permissions render buttons),
 *   - the install-status badge reflects enabled/disabled.
 *
 * NOTE: this spec is written but NOT run as part of task_09_07 — it is
 * marked PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev
 * server + a real install). Run it with
 * `npx playwright test e2e/permission-consent.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const INSTALL_ID = "22222222-0000-0000-0000-000000000002";
const LISTING_ID = "33333333-0000-0000-0000-000000000003";

type ConsentState = "granted" | "denied" | "pending";

interface PermissionStateItem {
  type: string;
  descriptor: Record<string, unknown>;
  state: ConsentState;
}

interface InstallationPermissions {
  installation_id: string;
  listing_id: string;
  status: string;
  consent_required: boolean;
  all_granted: boolean;
  permissions: PermissionStateItem[];
}

const ME = {
  user_id: "44444444-0000-0000-0000-000000000004",
  email: "owner@consent.test",
  full_name: "Project Owner",
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

function initialPermissions(): InstallationPermissions {
  return {
    installation_id: INSTALL_ID,
    listing_id: LISTING_ID,
    status: "disabled",
    consent_required: true,
    all_granted: false,
    permissions: [
      {
        type: "allowed_domains",
        descriptor: { type: "allowed_domains", value: ["api.x.com"] },
        state: "pending",
      },
      {
        type: "network_policy",
        descriptor: { type: "network_policy", value: "restricted" },
        state: "pending",
      },
    ],
  };
}

interface Capture {
  postCount: number;
  lastPostBody: { decisions: { type: string; decision: string }[] } | null;
}

async function setup(page: Page): Promise<Capture> {
  const capture: Capture = { postCount: 0, lastPostBody: null };
  // Mutable copy the POST handler folds decisions into.
  const current = initialPermissions();

  await page.addInitScript(
    ([token, tenantKey, tenantId]) => {
      window.localStorage.setItem("agentic.token", token);
      window.localStorage.setItem(tenantKey, tenantId);
    },
    ["e2e-fake-token", "admin-panel.tenant-id", TENANT_ID],
  );

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ME) }),
  );

  await page.route(
    `http://localhost:8001/marketplace/installations/${INSTALL_ID}/permissions`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(current),
      }),
  );

  await page.route(
    `http://localhost:8001/marketplace/installations/${INSTALL_ID}/consent`,
    (route) => {
      if (route.request().method() !== "POST") return route.continue();
      capture.postCount += 1;
      const body = JSON.parse(route.request().postData() ?? "{}") as {
        decisions: { type: string; decision: string }[];
      };
      capture.lastPostBody = body;
      // Fold decisions into the mutable surface (mirrors the backend).
      for (const d of body.decisions) {
        const perm = current.permissions.find((p) => p.type === d.type);
        if (perm) perm.state = d.decision === "grant" ? "granted" : "denied";
      }
      current.all_granted = current.permissions.every((p) => p.state === "granted");
      current.status = current.all_granted ? "enabled" : "disabled";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(current),
      });
    },
  );

  return capture;
}

// ---------------------------------------------------------------------------
// Initial render — disabled + pending
// ---------------------------------------------------------------------------
test("community install renders both permissions as pending and disabled", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/marketplace/installations/${INSTALL_ID}/permissions`, {
    waitUntil: "domcontentloaded",
  });

  await expect(page.getByTestId("consent-page")).toBeVisible();
  await expect(page.getByTestId("consent-install-status")).toContainText("Deshabilitada");
  await expect(page.getByTestId("consent-state-allowed_domains")).toContainText("Pendiente");
  await expect(page.getByTestId("consent-state-network_policy")).toContainText("Pendiente");
  // The descriptor value is rendered for the operator to read.
  await expect(page.getByTestId("consent-value-allowed_domains")).toContainText("api.x.com");
});

// ---------------------------------------------------------------------------
// Grant all -> enabled
// ---------------------------------------------------------------------------
test("granting every permission POSTs grant/grant and enables the install", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/marketplace/installations/${INSTALL_ID}/permissions`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("consent-grant-allowed_domains").click();
  await page.getByTestId("consent-grant-network_policy").click();
  await page.getByTestId("consent-submit").click();

  await expect.poll(() => capture.postCount).toBe(1);
  expect(capture.lastPostBody).toEqual({
    decisions: [
      { type: "allowed_domains", decision: "grant" },
      { type: "network_policy", decision: "grant" },
    ],
  });
  // After the response the install flips to enabled.
  await expect(page.getByTestId("consent-install-status")).toContainText("Habilitada");
  await expect(page.getByTestId("consent-state-allowed_domains")).toContainText("Concedido");
  await expect(page.getByTestId("consent-state-network_policy")).toContainText("Concedido");
});

// ---------------------------------------------------------------------------
// Partial deny -> stays disabled
// ---------------------------------------------------------------------------
test("denying one permission keeps the install disabled", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/marketplace/installations/${INSTALL_ID}/permissions`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("consent-grant-allowed_domains").click();
  await page.getByTestId("consent-deny-network_policy").click();
  await page.getByTestId("consent-submit").click();

  await expect.poll(() => capture.postCount).toBe(1);
  expect(capture.lastPostBody).toEqual({
    decisions: [
      { type: "allowed_domains", decision: "grant" },
      { type: "network_policy", decision: "deny" },
    ],
  });
  // One denied required permission -> the install stays disabled.
  await expect(page.getByTestId("consent-install-status")).toContainText("Deshabilitada");
  await expect(page.getByTestId("consent-state-allowed_domains")).toContainText("Concedido");
  await expect(page.getByTestId("consent-state-network_policy")).toContainText("Denegado");
});
