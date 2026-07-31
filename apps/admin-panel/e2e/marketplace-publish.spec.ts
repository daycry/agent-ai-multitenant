import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the publish discoverability + UX work (Plan 09.1 task_09_1_02).
 *
 * Two surfaces:
 *   1. /admin/marketplace — a prominent, tenant_admin-only "Publicar" CTA
 *      (header + an in-catalog callout) that links to the private publish
 *      screen, so publishing is OBVIOUS from the main marketplace page.
 *   2. /admin/marketplace/private — the publish screen gains insertable
 *      VALID examples ("usar ejemplo"), inline format help per manifest kind,
 *      and clear validation feedback that surfaces the backend 422 parser
 *      message (FastAPI's {"detail": "..."}).
 *
 * Mocks the backend so the test runs fully offline:
 *   - GET    /me                                — tenant_admin membership
 *   - GET    /marketplace/listings              — the catalog (own private + global)
 *   - POST   /marketplace/private/listings      — publish (201 ok / 422 malformed)
 *
 * NOTE: this spec is WRITTEN but NOT run as part of task_09_1_02 — it is
 * marked PENDING HUMAN VERIFICATION (needs a browser + the admin-panel dev
 * server). Run it with `npx playwright test e2e/marketplace-publish.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const PRIVATE_ID = "22222222-0000-0000-0000-000000000002";
const GLOBAL_ID = "33333333-0000-0000-0000-000000000003";
const NEW_ID = "44444444-0000-0000-0000-000000000004";

const ME = {
  user_id: "55555555-0000-0000-0000-000000000005",
  email: "admin@publish.test",
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

async function setup(page: Page): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });

  await page.route("http://localhost:8001/me", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ME) }),
  );

  await page.route("http://localhost:8001/marketplace/listings**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([PRIVATE_LISTING, GLOBAL_LISTING]),
    }),
  );
}

// ---------------------------------------------------------------------------
// Discoverability — the main catalog page surfaces a Publicar CTA
// ---------------------------------------------------------------------------
test("the marketplace catalog exposes a prominent Publicar CTA to the publish screen", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("marketplace-admin-page")).toBeVisible();

  // Header CTA -> private publish screen.
  await expect(page.getByTestId("marketplace-publish-cta")).toHaveAttribute(
    "href",
    "/admin/marketplace/private",
  );

  // In-catalog callout CTA -> private publish screen.
  await expect(page.getByTestId("catalog-publish-callout")).toBeVisible();
  await expect(page.getByTestId("catalog-publish-cta")).toHaveAttribute(
    "href",
    "/admin/marketplace/private",
  );
});

// ---------------------------------------------------------------------------
// Format help — the publish screen shows what each manifest kind needs
// ---------------------------------------------------------------------------
test("the publish screen shows inline format help that updates with the kind", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/marketplace/private", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("private-format-help")).toBeVisible();
  // Default kind is skill — the SKILL.md frontmatter summary.
  await expect(page.getByTestId("private-format-help-summary")).toContainText("SKILL.md");

  // Switching to tool updates the help to the YAML manifest summary.
  await page.getByTestId("private-kind-select").selectOption("tool");
  await expect(page.getByTestId("private-format-help-summary")).toContainText("YAML");
});

// ---------------------------------------------------------------------------
// Usar ejemplo — inserts a valid manifest and publishing POSTs it
// ---------------------------------------------------------------------------
test("usar ejemplo inserts a valid manifest and publishing POSTs kind + manifest", async ({
  page,
}) => {
  await setup(page);

  let posted: { kind?: string; manifest?: string } = {};
  await page.route("http://localhost:8001/marketplace/private/listings", async (route) => {
    posted = route.request().postDataJSON() as { kind?: string; manifest?: string };
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ ...PRIVATE_LISTING, id: NEW_ID, name: "internal-reporter" }),
    });
  });

  await page.goto("/admin/marketplace/private", { waitUntil: "domcontentloaded" });

  // The manifest box starts empty; "usar ejemplo" fills it with a valid SKILL.md.
  await expect(page.getByTestId("private-manifest")).toHaveValue("");
  await page.getByTestId("private-use-example").click();
  await expect(page.getByTestId("private-manifest")).toContainText("name: internal-reporter");

  await page.getByTestId("private-publish-submit").click();

  await expect.poll(() => posted.kind).toBe("skill");
  expect(posted.manifest).toContain("internal-reporter");
  await expect(page.getByTestId("private-publish-success")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Validation feedback — a 422 surfaces the parser message clearly
// ---------------------------------------------------------------------------
test("a malformed manifest surfaces the backend 422 parser message", async ({ page }) => {
  await setup(page);

  await page.route("http://localhost:8001/marketplace/private/listings", (route) =>
    route.fulfill({
      status: 422,
      contentType: "application/json",
      // FastAPI's HTTPException(detail=str(exc)) shape — the parser message.
      body: JSON.stringify({
        detail: "SKILL.md must begin with a YAML frontmatter block fenced by '---' lines",
      }),
    }),
  );

  await page.goto("/admin/marketplace/private", { waitUntil: "domcontentloaded" });

  await page.getByTestId("private-manifest").fill("this is not a valid manifest");
  await page.getByTestId("private-publish-submit").click();

  const error = page.getByTestId("private-publish-error");
  await expect(error).toBeVisible();
  // The exact parser message is surfaced (not the raw {"detail":...} envelope).
  await expect(error).toContainText("YAML frontmatter block");
});
