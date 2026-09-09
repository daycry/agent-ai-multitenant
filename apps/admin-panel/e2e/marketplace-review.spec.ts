import { expect, test, type Page, type Route } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E (subset mockeado, sin backend) de la cola de revisión del marketplace
 * (ADR 0142 D6) y de cómo se LLEGA a ella (`task_mk_13`, UI-05):
 *
 *   - la cabecera del marketplace enlaza la cola para el System Admin;
 *   - la cola lista lo `pending_review` y «Aprobar» hace el POST correcto;
 *   - tras aprobar, la cola se refresca y queda vacía;
 *   - el sidebar (grupo Plataforma) tiene su entrada.
 */
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const SYSTEM_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000001",
  email: "root@platform.test",
  full_name: "System Admin",
  is_system_admin: true,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

const PENDING = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  tenant_id: TENANT_ID,
  kind: "tool",
  name: "informe-interno",
  version: "1.0.0",
  description: "Genera el informe semanal.",
  author: "Equipo Datos",
  trust_level: "community",
  review_status: "pending_review",
  reviewed_at: null,
  rejection_reason: null,
  manifest: { implementation_type: "http_endpoint" },
  requested_permissions: [],
  created_at: "2026-09-01T00:00:00Z",
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function setup(page: Page): Promise<{ approved: string[]; queue: unknown[] }> {
  const state = { approved: [] as string[], queue: [PENDING] as unknown[] };
  await seedSession(page);
  await page.route(apiRoute("/me"), (route) => json(route, SYSTEM_ADMIN));
  await page.route(apiRoute("/marketplace/**"), (route) => json(route, []));
  await page.route(apiRoute("/admin/marketplace/review-queue**"), (route) =>
    json(route, state.queue),
  );
  await page.route(apiRoute(`/admin/marketplace/listings/${PENDING.id}/versions`), (route) =>
    json(route, []),
  );
  await page.route(apiRoute(`/admin/marketplace/listings/${PENDING.id}/approve`), (route) => {
    state.approved.push(route.request().method());
    state.queue = [];
    return json(route, { ...PENDING, review_status: "published" });
  });
  return state;
}

test("desde la cabecera del marketplace se llega a la cola y se aprueba un listing", async ({
  page,
}) => {
  const state = await setup(page);
  await page.goto("/admin/marketplace", { waitUntil: "domcontentloaded" });

  await page.getByTestId("marketplace-review-link").click();
  await expect(page).toHaveURL(/\/admin\/marketplace\/review$/);

  const card = page.getByTestId(`review-card-${PENDING.name}`);
  await expect(card).toBeVisible();
  await card.getByTestId("approve").click();

  await expect.poll(() => state.approved).toEqual(["POST"]);
  await expect(page.getByTestId("review-queue-empty")).toBeVisible();
});

test("el sidebar de Plataforma tiene la entrada de la cola de revisión", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/marketplace/review", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("review-status-filter")).toBeVisible();

  const link = page.getByTestId("nav-review");
  if (!(await link.isVisible())) {
    await page.getByTestId("nav-group-plataforma").click();
  }
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", "/admin/marketplace/review");
});
