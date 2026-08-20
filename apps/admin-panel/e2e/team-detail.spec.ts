import { expect, test, type Page } from "@playwright/test";
import { navigateVia } from "./helpers/nav";
import { sessionToken } from "./helpers/session";

/**
 * E2E for the Team Detail screen (task_01_20).
 *
 * Verifies:
 *   - The teams list lands and shows every built-in team (5 del seed base + el
 *     de CodeIgniter 4, que se siembra aparte).
 *   - The detail page renders members with leader badge + scope tag.
 *   - The "Add member" button is disabled on built-in (read-only) teams.
 *   - On a tenant-owned team, the dialog opens, the linked/forked
 *     radio works, and "forked" forces a project selection.
 *
 * Pre-conditions: dev stack up, seeds applied (run-e2e.ps1 handles it),
 * admin user present.
 */
const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "root@example.com";
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "longenoughpw";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByLabel(/^(password|contraseña)$/i).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /^(sign in|iniciar sesión)$/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);
}

test("teams list shows every built-in team", async ({ page }) => {
  await login(page);
  // El grupo `recursos` de la nav arranca cerrado (ver `helpers/nav.ts`).
  await navigateVia(page, "recursos", "nav-teams");
  await expect(page).toHaveURL(/\/admin\/teams$/);

  await expect(page.getByTestId("teams-grid")).toBeVisible();

  // Count built-in cards specifically: tenant-side runs accumulate
  // their own teams in the dev DB, so the invariant we care about is
  // "los built-in están visibles", no "el grid tiene N tarjetas".
  //
  // El título dice 5 y el seed lleva 6 desde que se mergeó el equipo built-in
  // de CodeIgniter 4 (PR #33): `seed_builtin_teams` sigue sembrando 5 —su test
  // fija ese número— y `seed_ci4_team` añade el sexto por su cuenta,
  // precisamente para no tocarlo. Este spec afirmaba la suma, así que lo que
  // envejeció fue la premisa, no el producto. Se comprueban por NOMBRE los seis
  // que el seed promete: eso es más fuerte que un conteo (un built-in que
  // desaparezca se ve aunque otro entre a la vez) y no vuelve a caducar cuando
  // el catálogo crezca.
  const builtinCards = page
    .getByTestId("teams-grid")
    .locator("[data-testid^=team-]")
    .filter({ has: page.getByTestId("team-builtin-badge") });
  await expect(builtinCards).not.toHaveCount(0);

  const grid = page.getByTestId("teams-grid");
  for (const name of [
    "Equipo Full-Stack Web",
    "Equipo DevOps & Platform",
    "Equipo Backend / API",
    "Equipo Data",
    "Equipo Research & Spec",
    "CodeIgniter 4",
  ]) {
    // Por CABECERA y exacta: el nombre del equipo aparece TAMBIEN dentro de su
    // descripcion ("Equipo built-in para proyectos CodeIgniter 4 ..."), asi que
    // un `getByText` laxo resuelve a dos nodos y muere en modo estricto.
    await expect(
      grid.getByRole("heading", { name, exact: true }),
      `falta el equipo built-in "${name}"`,
    ).toBeVisible();
  }
});

test("built-in team detail renders members and locks add-member", async ({ page }) => {
  await login(page);
  await page.goto("/admin/teams");
  // The card title is an <h3>, not a link. Navigate through the
  // explicit "Ver detalle" link inside the Full-Stack Web card.
  const fullStackCard = page
    .getByTestId("teams-grid")
    .locator("[data-testid^=team-]")
    .filter({ hasText: "Equipo Full-Stack Web" });
  await fullStackCard.getByRole("link", { name: /ver detalle/i }).click();

  // The detail header carries the team name and a "Built-in" badge.
  await expect(page.getByTestId("team-name")).toHaveText("Equipo Full-Stack Web");
  await expect(page.getByTestId("team-detail")).toBeVisible();

  // Full-Stack Web seed has 6 members. `[data-testid^=member-]` casa TRES nodos
  // por miembro desde que cada fila publica además su modo y su prioridad
  // (`member-linked-{id}` / `member-priority-{id}`): 6 miembros daban 18 nodos.
  // El seed sigue sembrando 6 —lo que envejeció fue el selector, no el dato—,
  // así que se acota a la fila en sí en vez de relajar el número.
  const members = page
    .getByTestId("members-list")
    .locator(
      "[data-testid^=member-]:not([data-testid^=member-linked-]):not([data-testid^=member-priority-])",
    );
  await expect(members).toHaveCount(6);

  // Exactly one leader badge.
  await expect(page.getByTestId("leader-badge")).toHaveCount(1);

  // Add member is disabled on a built-in team.
  await expect(page.getByTestId("add-member-button")).toBeDisabled();
});

// Unblocked by the superadmin cross-tenant wiring:
//   1. /auth/register auto-promotes the first user to system_admin,
//      so root@example.com lands as superadmin.
//   2. Superadmin writes accept the `X-Tenant-Id` header in lieu
//      of a JWT `tid` claim.
//   3. apiFetch injects that header from localStorage automatically.
//
// Setup creates a throwaway tenant via /admin/tenants, parks its
// id in localStorage so the panel's own fetches scope to it, and
// creates the team via POST /teams with the header set explicitly
// (page.request bypasses the apiFetch wrapper). The dialog flow
// itself is unchanged.
test("add-member dialog enforces project selection in fork mode", async ({ page }) => {
  await login(page);

  await page.goto("/admin/teams");
  // ADR 0133: the session is an httpOnly cookie, so the PAGE cannot read it —
  // that is the point. Playwright can, from the browser context, which is what
  // this spec needs to sign its own `page.request` calls (page.request bypasses
  // `apiFetch`, so no cookie/CSRF wiring happens for it).
  const token: string | null = await sessionToken(page);
  expect(token).toBeTruthy();

  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001";

  // Each run gets its own tenant — the slug carries a random suffix
  // because the dev DB persists across local runs and the slug has
  // a unique index.
  const slug = `e2e-team-${Date.now().toString(36)}`;
  const tenantResp = await page.request.post(`${apiBase}/admin/tenants`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: `E2E team tenant ${slug}`, slug },
  });
  expect(tenantResp.status(), await tenantResp.text()).toBe(201);
  const tenant = await tenantResp.json();

  // Make the panel act as that tenant on every subsequent apiFetch.
  await page.evaluate((id: string) => localStorage.setItem("admin-panel.tenant-id", id), tenant.id);

  // Create the editable team. page.request doesn't pass through
  // apiFetch so the header has to be set inline here.
  const teamResp = await page.request.post(`${apiBase}/teams`, {
    headers: { Authorization: `Bearer ${token}`, "X-Tenant-Id": tenant.id },
    data: { name: "E2E test team" },
  });
  expect(teamResp.status(), await teamResp.text()).toBe(201);
  const team = await teamResp.json();

  await page.goto(`/admin/teams/${team.id}`);
  await expect(page.getByTestId("team-name")).toHaveText("E2E test team");

  // Add member is enabled here.
  const addBtn = page.getByTestId("add-member-button");
  await expect(addBtn).toBeEnabled();
  await addBtn.click();

  // Dialog opens.
  await expect(page.getByTestId("add-member-dialog")).toBeVisible();

  // Pick an agent.
  await page.getByTestId("agent-select").selectOption({ index: 1 }); // first non-placeholder option

  // Default mode is linked: project select must NOT be visible.
  await expect(page.getByTestId("project-select")).toHaveCount(0);

  // Switch to forked: project select appears, submit stays disabled
  // until a project is picked.
  await page.getByTestId("mode-forked").check();
  await expect(page.getByTestId("project-select")).toBeVisible();
  await expect(page.getByTestId("add-member-submit")).toBeDisabled();

  // Switch back to linked: project select disappears, submit enables.
  await page.getByTestId("mode-linked").check();
  await expect(page.getByTestId("project-select")).toHaveCount(0);
  await expect(page.getByTestId("add-member-submit")).toBeEnabled();
});
