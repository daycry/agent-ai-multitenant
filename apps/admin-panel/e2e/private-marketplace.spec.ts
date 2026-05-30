import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for /admin/marketplace/private — the private tenant marketplace where a
 * tenant publishes + lists its OWN internal skills/tools as PRIVATE listings
 * (Plan 09 task_09_16).
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET    /me                                — tenant_admin membership
 *   - GET    /marketplace/listings              — the catalog (the tenant's
 *                                                 own private listings + a
 *                                                 global one; the page filters
 *                                                 the global one OUT of the
 *                                                 private catalog view)
 *   - POST   /marketplace/private/listings      — publish (echoes the parsed
 *                                                 private listing)
 *   - DELETE /marketplace/private/listings/{id} — unpublish (204)
 *
 * Drives:
 *   - the page lists ONLY the tenant's own private listing (the global
 *     catalog listing is filtered out of the private view),
 *   - the publish form is visible to a tenant_admin,
 *   - publishing a SKILL.md manifest POSTs the kind + manifest and refreshes,
 *   - unpublishing a listing DELETEs it.
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_09_16 — it is marked
 * PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev server).
 * Run it with `npx playwright test e2e/private-marketplace.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const PRIVATE_ID = "22222222-0000-0000-0000-000000000002";
const GLOBAL_ID = "33333333-0000-0000-0000-000000000003";
const NEW_ID = "44444444-0000-0000-0000-000000000004";

const ME = {
  user_id: "55555555-0000-0000-0000-000000000005",
  email: "admin@private.test",
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

// The catalog the browse endpoint returns: one OWN private listing
// (tenant_id = TENANT_ID) + one global catalog listing (tenant_id = null).
// The page must show only the private one in the private catalog view.
const PRIVATE_LISTING = {
  id: PRIVATE_ID,
  source_id: "66666666-0000-0000-0000-000000000006",
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
  source_id: "77777777-0000-0000-0000-000000000007",
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

const SKILL_MANIFEST = `---
name: internal-deployer
description: Deploys the internal service.
version: 1.0.0
permissions:
  network_policy: none
---

# Internal Deployer
`;

async function setup(page: Page): Promise<void> {
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

  // Browse returns both the private + global listing; the page filters.
  await page.route("http://localhost:8001/marketplace/listings**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([PRIVATE_LISTING, GLOBAL_LISTING]),
    }),
  );
}

// ---------------------------------------------------------------------------
// List — only the tenant's own private listing (global filtered out)
// ---------------------------------------------------------------------------
test("lists only the tenant's own private listings", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/marketplace/private", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("private-marketplace-page")).toBeVisible();
  await expect(page.getByTestId("private-listing-list")).toBeVisible();
  await expect(page.getByTestId(`private-listing-${PRIVATE_ID}`)).toBeVisible();
  // The global catalog listing is NOT part of the private view.
  await expect(page.getByTestId(`private-listing-${GLOBAL_ID}`)).toHaveCount(0);
  await expect(page.getByTestId(`private-listing-kind-${PRIVATE_ID}`)).toContainText("skill");
});

// ---------------------------------------------------------------------------
// Publish form visible to tenant_admin
// ---------------------------------------------------------------------------
test("shows the publish form to a tenant_admin", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/marketplace/private", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("private-publish-card")).toBeVisible();
  await expect(page.getByTestId("private-kind-select")).toBeVisible();
  await expect(page.getByTestId("private-manifest")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Publish — POSTs the kind + manifest
// ---------------------------------------------------------------------------
test("publishing a SKILL.md manifest POSTs kind + manifest", async ({ page }) => {
  await setup(page);

  let posted: { kind?: string; manifest?: string } = {};
  await page.route("http://localhost:8001/marketplace/private/listings", async (route) => {
    posted = route.request().postDataJSON() as { kind?: string; manifest?: string };
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ ...PRIVATE_LISTING, id: NEW_ID, name: "internal-deployer" }),
    });
  });

  await page.goto("/admin/marketplace/private", { waitUntil: "domcontentloaded" });

  await page.getByTestId("private-kind-select").selectOption("skill");
  await page.getByTestId("private-manifest").fill(SKILL_MANIFEST);
  await page.getByTestId("private-publish-submit").click();

  await expect.poll(() => posted.kind).toBe("skill");
  expect(posted.manifest).toContain("internal-deployer");
});

// ---------------------------------------------------------------------------
// Unpublish — DELETEs the listing
// ---------------------------------------------------------------------------
test("unpublishing a listing DELETEs it", async ({ page }) => {
  await setup(page);

  let deletedPath: string | null = null;
  await page.route(`http://localhost:8001/marketplace/private/listings/${PRIVATE_ID}`, (route) => {
    deletedPath = new URL(route.request().url()).pathname;
    return route.fulfill({ status: 204, body: "" });
  });

  await page.goto("/admin/marketplace/private", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`private-unpublish-${PRIVATE_ID}`).click();

  await expect.poll(() => deletedPath).toBe(`/marketplace/private/listings/${PRIVATE_ID}`);
});
