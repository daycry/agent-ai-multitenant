import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the docs visor sidebar (Plan 07 Fase D, task_07_11).
 *
 * Covers: the route renders the admin shell + sidebar; the sidebar lists only
 * the projects the API returns (RBAC/RLS is enforced server-side, so a project
 * the user can't see never appears in `/projects`); expanding a project lazily
 * fetches its `/docs/tree`; expanding a folder reveals its `.md` files; and
 * selecting a file deep-links it into the URL (`?project=&path=`).
 *
 * NOTE: written for later human verification — not run in this environment
 * (no app+browser here).
 */

const PROJECT_A = "11111111-1111-1111-1111-111111111111";
const PROJECT_B = "22222222-2222-2222-2222-222222222222";

const PROJECTS_FIXTURE = [
  { id: PROJECT_A, name: "Proyecto A", status: "active" },
  { id: PROJECT_B, name: "Proyecto B", status: "active" },
];

const TREE_A = {
  project_id: PROJECT_A,
  folders: [
    {
      type: "folder",
      name: "01-overview",
      relpath: "docs/01-overview",
      folders: [],
      files: [
        {
          type: "file",
          name: "vision.md",
          relpath: "docs/01-overview/vision.md",
          size_bytes: 1234,
        },
      ],
    },
  ],
  files: [{ type: "file", name: "README.md", relpath: "docs/README.md", size_bytes: 42 }],
};

const EMPTY_TREE = { project_id: PROJECT_B, folders: [], files: [] };

async function setup(page: Page, projects: object[]): Promise<void> {
  await seedSession(page);
  await page.route("**/projects", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(projects),
    }),
  );
  await page.route(`**/projects/${PROJECT_A}/docs/tree`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TREE_A),
    }),
  );
  await page.route(`**/projects/${PROJECT_B}/docs/tree`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(EMPTY_TREE),
    }),
  );
}

test("docs visor renders the sidebar with accessible projects", async ({ page }) => {
  await setup(page, PROJECTS_FIXTURE);
  await page.goto("/admin/docs", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("docs-visor")).toBeVisible();
  await expect(page.getByTestId("docs-sidebar")).toBeVisible();
  await expect(page.getByTestId(`docs-project-${PROJECT_A}`)).toBeVisible();
  await expect(page.getByTestId(`docs-project-${PROJECT_B}`)).toBeVisible();
  // Nothing selected yet → empty content pane.
  await expect(page.getByTestId("docs-content-empty")).toBeVisible();
});

test("expanding a project loads its tree and selecting a file deep-links it", async ({ page }) => {
  await setup(page, PROJECTS_FIXTURE);
  await page.goto("/admin/docs", { waitUntil: "domcontentloaded" });

  // Expand project A → its root README plus the 01-overview folder appear.
  await page.getByTestId(`docs-project-toggle-${PROJECT_A}`).click();
  await expect(page.getByTestId(`docs-file-${PROJECT_A}-docs/README.md`)).toBeVisible();
  await expect(page.getByTestId(`docs-folder-${PROJECT_A}-docs/01-overview`)).toBeVisible();

  // Expand the folder → its nested file appears.
  await page.getByTestId(`docs-folder-${PROJECT_A}-docs/01-overview`).click();
  const nested = page.getByTestId(`docs-file-${PROJECT_A}-docs/01-overview/vision.md`);
  await expect(nested).toBeVisible();

  // Select the nested file → URL carries project + path, pane reflects it.
  await nested.click();
  await expect(page).toHaveURL(
    new RegExp(`project=${PROJECT_A}&path=docs%2F01-overview%2Fvision\\.md`),
  );
  await expect(page.getByTestId("docs-selected-path")).toHaveText("docs/01-overview/vision.md");
});

test("a project with no docs shows an empty-tree message", async ({ page }) => {
  await setup(page, PROJECTS_FIXTURE);
  await page.goto("/admin/docs", { waitUntil: "domcontentloaded" });

  await page.getByTestId(`docs-project-toggle-${PROJECT_B}`).click();
  await expect(page.getByTestId("docs-tree-empty")).toBeVisible();
});

test("only API-returned projects are rendered (cross-tenant scoping)", async ({ page }) => {
  // The API hides cross-tenant projects, so the fixture omits PROJECT_B; the
  // sidebar must render Proyecto A only.
  await setup(page, [PROJECTS_FIXTURE[0]]);
  await page.goto("/admin/docs", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId(`docs-project-${PROJECT_A}`)).toBeVisible();
  await expect(page.getByTestId(`docs-project-${PROJECT_B}`)).toHaveCount(0);
});

test("deep-link opens the project section and pre-selects the file", async ({ page }) => {
  await setup(page, PROJECTS_FIXTURE);
  await page.goto(`/admin/docs?project=${PROJECT_A}&path=${encodeURIComponent("docs/README.md")}`, {
    waitUntil: "domcontentloaded",
  });

  // Section auto-opens because it matches the selected project.
  const file = page.getByTestId(`docs-file-${PROJECT_A}-docs/README.md`);
  await expect(file).toBeVisible();
  await expect(file).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("docs-selected-path")).toHaveText("docs/README.md");
});
