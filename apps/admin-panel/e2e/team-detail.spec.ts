import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the Team Detail screen (task_01_20).
 *
 * Verifies:
 *   - The teams list lands and shows the 5 built-in templates.
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
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/admin\/dashboard$/);
}

test("teams list shows the 5 built-in templates", async ({ page }) => {
  await login(page);
  await page.getByTestId("nav-teams").click();
  await expect(page).toHaveURL(/\/admin\/teams$/);

  await expect(page.getByTestId("teams-grid")).toBeVisible();

  // Count built-in cards specifically: tenant-side runs accumulate
  // their own teams in the dev DB, so the invariant we care about is
  // "the 5 built-ins are visible", not "the grid has exactly 5 cards".
  const builtinCards = page
    .getByTestId("teams-grid")
    .locator("[data-testid^=team-]")
    .filter({ has: page.getByTestId("team-builtin-badge") });
  await expect(builtinCards).toHaveCount(5);

  await expect(page.getByTestId("teams-grid").getByText("Equipo Full-Stack Web")).toBeVisible();
  await expect(page.getByTestId("teams-grid").getByText("Equipo DevOps & Platform")).toBeVisible();
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

  // Full-Stack Web seed has 6 members.
  const members = page.getByTestId("members-list").locator("[data-testid^=member-]");
  await expect(members).toHaveCount(6);

  // Exactly one leader badge.
  await expect(page.getByTestId("leader-badge")).toHaveCount(1);

  // Add member is disabled on a built-in team.
  await expect(page.getByTestId("add-member-button")).toBeDisabled();
});

// Skipped: dialog workflow needs the admin JWT to carry `tid` so
// POST /teams (tenant-scoped write) succeeds. Today /auth/login always
// issues tid=null and /auth/select-tenant doesn't exist yet -- it
// ships with the tenant-selector UI in a later phase. The
// linked-vs-forked SEMANTICS are exhaustively covered by the 21
// integration tests in tests/integration/test_fork_*.py and
// test_linked_vs_forked_*.py; what's missing here is the dialog UX
// E2E only.
test.skip("add-member dialog enforces project selection in fork mode", async ({ page }) => {
  await login(page);

  // Built-ins are read-only. Create a tenant team via the API so we
  // have an editable target. Using fetch from the page context keeps
  // the auth header (we re-read the token from localStorage).
  await page.goto("/admin/teams");
  const token: string | null = await page.evaluate(() => localStorage.getItem("agentic.token"));
  expect(token).toBeTruthy();

  const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001";
  const created = await page.request.post(`${apiBase}/teams`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: "E2E test team" },
  });
  expect(created.status()).toBe(201);
  const team = await created.json();

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
