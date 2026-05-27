import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the documents index (Plan 06.6 task_06_6_11).
 */

const PROJECTS_FIXTURE = [
  {
    id: "p1-aaaa-bbbb-cccc-dddddddddddd",
    name: "Backend Core",
    description: "API + DB",
    status: "active",
  },
  {
    id: "p2-aaaa-bbbb-cccc-dddddddddddd",
    name: "Frontend",
    description: null,
    status: "active",
  },
];

async function setup(page: Page, projects: object[]): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/projects", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(projects),
    }),
  );
}

test("documents index lists projects with links to their KBs", async ({ page }) => {
  await setup(page, PROJECTS_FIXTURE);
  await page.goto("/admin/documents", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("documents-index")).toBeVisible();
  for (const project of PROJECTS_FIXTURE) {
    const link = page.getByTestId(`documents-project-link-${project.id}`);
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", `/admin/projects/${project.id}/knowledge-bases`);
  }
});

test("documents index shows empty state when no projects", async ({ page }) => {
  await setup(page, []);
  await page.goto("/admin/documents", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("documents-empty")).toBeVisible();
});
