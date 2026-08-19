import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for clickable project cards (Plan 06.6 task_06_6_04).
 *
 * Verifies that clicking a card in /admin/projects navigates to
 * the hub page /admin/projects/{id}.
 */

const PROJECTS_FIXTURE = [
  {
    id: "aaaaaaaa-1111-2222-3333-444444444444",
    name: "Proyecto Click",
    description: "card debe ser clickable",
    status: "active",
    team_id: null,
    is_template: false,
  },
];

async function setup(page: Page): Promise<void> {
  await seedSession(page);
  await page.route(apiRoute("/projects"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECTS_FIXTURE),
    }),
  );
}

test("card has a link wrapper pointing to the hub", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/projects", { waitUntil: "domcontentloaded" });

  const link = page.getByTestId(`project-link-${PROJECTS_FIXTURE[0].id}`);
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", `/admin/projects/${PROJECTS_FIXTURE[0].id}`);
});

test("clicking the card navigates to the hub URL", async ({ page }) => {
  await setup(page);
  // Mock the hub backend so we don't get a 500 on arrival.
  await page.route(apiRoute(`/projects/${PROJECTS_FIXTURE[0].id}`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...PROJECTS_FIXTURE[0],
        tenant_id: "t",
        mcp_servers: [],
        rag_knowledge_bases: [],
        worker_config: {},
        repository_config: null,
        human_approval_policy: null,
        secrets_vault_id: null,
        budget_amount: null,
        budget_currency: null,
        budget_period: null,
        budget_period_start_day: null,
        budget_period_length_days: null,
        paused_by_budget: false,
        created_at: "2026-05-27T12:00:00Z",
        updated_at: "2026-05-27T12:00:00Z",
        deleted_at: null,
      }),
    }),
  );

  await page.goto("/admin/projects", { waitUntil: "domcontentloaded" });
  await page.getByTestId(`project-link-${PROJECTS_FIXTURE[0].id}`).click();
  await page.waitForURL(`**/admin/projects/${PROJECTS_FIXTURE[0].id}`);
  await expect(page.getByTestId("project-hub")).toBeVisible();
});
