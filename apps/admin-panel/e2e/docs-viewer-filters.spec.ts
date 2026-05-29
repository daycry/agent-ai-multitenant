import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the docs visor filters + bookmarks (Plan 07 Fase D, task_07_15).
 *
 * Covers:
 *   * Facet filters (category + type) prune the sidebar tree.
 *   * Filters also narrow search hits (client-side over the RBAC-scoped hits).
 *   * Starring a doc from the tree persists it (tenant-scoped localStorage) and
 *     surfaces it in the Marcadores tab; the count badge updates.
 *   * The bookmarks view opens a starred doc (deep-links it) and can un-star it.
 *   * Bookmarks survive a reload (localStorage), and the recency filter narrows
 *     the list.
 *   * Filters are disabled until a project is selected.
 *
 * NOTE: written for later human verification — not run in this environment
 * (no app+browser here).
 */

const PROJECT_A = "11111111-1111-1111-1111-111111111111";
const PROJECT_NAME = "Proyecto A";

const PROJECTS_FIXTURE = [{ id: PROJECT_A, name: PROJECT_NAME, status: "active" }];

// A tree spanning two canonical categories so a category filter is observable.
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
    {
      type: "folder",
      name: "05-architecture-decisions",
      relpath: "docs/05-architecture-decisions",
      folders: [],
      files: [
        {
          type: "file",
          name: "0021-llm-providers.md",
          relpath: "docs/05-architecture-decisions/0021-llm-providers.md",
          size_bytes: 4096,
        },
      ],
    },
  ],
  files: [{ type: "file", name: "README.md", relpath: "docs/README.md", size_bytes: 42 }],
};

const VISION_MD = ["# Visión", "", "Contenido de la visión del proyecto."].join("\n");
const VISION_CONTENT = {
  project_id: PROJECT_A,
  relpath: "docs/01-overview/vision.md",
  content: VISION_MD,
  size_bytes: VISION_MD.length,
};

const SEARCH_HITS = {
  project_id: PROJECT_A,
  query: "llm",
  hits: [
    {
      chunk_id: "chunk-ov",
      document_id: "doc-ov",
      relpath: "docs/01-overview/vision.md",
      ordinal: 0,
      rank: 1,
      snippet: "La visión menciona los proveedores LLM soportados.",
    },
    {
      chunk_id: "chunk-adr",
      document_id: "doc-adr",
      relpath: "docs/05-architecture-decisions/0021-llm-providers.md",
      ordinal: 2,
      rank: 2,
      snippet: "ADR sobre el catálogo cerrado de proveedores LLM.",
    },
  ],
};

const TENANT_ID = "tenant-e2e";

async function setup(page: Page): Promise<void> {
  await page.addInitScript(
    ([tenant]) => {
      window.localStorage.setItem("agentic.token", "e2e-fake-token");
      // Scope bookmarks to a known tenant so we can assert the storage key.
      window.localStorage.setItem("admin-panel.tenant-id", tenant);
    },
    [TENANT_ID],
  );
  await page.route("**/projects", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECTS_FIXTURE),
    }),
  );
  await page.route(`**/projects/${PROJECT_A}/docs/tree`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TREE_A),
    }),
  );
  await page.route(`**/projects/${PROJECT_A}/docs/content?**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(VISION_CONTENT),
    }),
  );
  await page.route(`**/projects/${PROJECT_A}/docs/search?**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(SEARCH_HITS),
    }),
  );
}

/** Open the visor scoped to project A and expand its tree. */
async function openTree(page: Page): Promise<void> {
  await page.goto(`/admin/docs?project=${PROJECT_A}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("docs-visor")).toBeVisible();
  await page.getByTestId(`docs-project-toggle-${PROJECT_A}`).click();
  await expect(page.getByTestId(`docs-file-${PROJECT_A}-docs/README.md`)).toBeVisible();
}

test("filters are disabled until a project is selected", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/docs", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("docs-filters-panel")).toBeVisible();
  // The category fieldset is disabled (no project to scope to).
  await expect(page.getByTestId("docs-filter-category-01-overview")).toBeDisabled();
});

test("a category filter prunes the sidebar tree", async ({ page }) => {
  await setup(page);
  await openTree(page);

  // Both category folders are present initially.
  await expect(page.getByTestId(`docs-folder-${PROJECT_A}-docs/01-overview`)).toBeVisible();
  await expect(
    page.getByTestId(`docs-folder-${PROJECT_A}-docs/05-architecture-decisions`),
  ).toBeVisible();

  // Filter to ADR (architecture-decisions) only.
  await page.getByTestId("docs-filter-category-05-architecture-decisions").click();

  await expect(
    page.getByTestId(`docs-folder-${PROJECT_A}-docs/05-architecture-decisions`),
  ).toBeVisible();
  // The overview folder and the root README are pruned out.
  await expect(page.getByTestId(`docs-folder-${PROJECT_A}-docs/01-overview`)).toHaveCount(0);
  await expect(page.getByTestId(`docs-file-${PROJECT_A}-docs/README.md`)).toHaveCount(0);

  // Clearing restores the full tree.
  await page.getByTestId("docs-filters-clear").click();
  await expect(page.getByTestId(`docs-folder-${PROJECT_A}-docs/01-overview`)).toBeVisible();
});

test("a type filter narrows search hits", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/docs?project=${PROJECT_A}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("docs-search-input").fill("llm");
  await expect(page.getByTestId("docs-search-hit-chunk-ov")).toBeVisible();
  await expect(page.getByTestId("docs-search-hit-chunk-adr")).toBeVisible();

  // Filter to ADRs → only the architecture-decisions hit remains.
  await page.getByTestId("docs-filter-type-adr").click();
  await expect(page.getByTestId("docs-search-hit-chunk-adr")).toBeVisible();
  await expect(page.getByTestId("docs-search-hit-chunk-ov")).toHaveCount(0);
});

test("starring a doc from the tree adds it to the bookmarks view", async ({ page }) => {
  await setup(page);
  await openTree(page);

  // Star the root README from the tree.
  await page.getByTestId(`docs-tree-star-${PROJECT_A}-docs/README.md`).click();

  // The count badge appears.
  await expect(page.getByTestId("docs-bookmarks-count")).toHaveText("1");

  // Switch to the Marcadores tab → the doc is listed.
  await page.getByTestId("docs-rail-tab-bookmarks").click();
  await expect(page.getByTestId(`docs-bookmark-${PROJECT_A}-docs/README.md`)).toBeVisible();

  // Opening it deep-links the doc.
  await page
    .getByTestId(`docs-bookmark-${PROJECT_A}-docs/README.md`)
    .getByTestId("docs-bookmark-open")
    .click();
  await expect(page).toHaveURL(/path=docs%2FREADME\.md/);
});

test("bookmarks persist across reload and can be removed", async ({ page }) => {
  await setup(page);
  await openTree(page);

  await page.getByTestId(`docs-tree-star-${PROJECT_A}-docs/README.md`).click();
  await expect(page.getByTestId("docs-bookmarks-count")).toHaveText("1");

  // Reload → the bookmark is restored from localStorage.
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("docs-bookmarks-count")).toHaveText("1");

  // Remove it from the bookmarks view.
  await page.getByTestId("docs-rail-tab-bookmarks").click();
  await page
    .getByTestId(`docs-bookmark-${PROJECT_A}-docs/README.md`)
    .getByTestId("docs-bookmark-remove")
    .click();
  await expect(page.getByTestId("docs-bookmarks-empty")).toBeVisible();
  await expect(page.getByTestId("docs-bookmarks-count")).toHaveCount(0);
});

test("the viewer header star toggles the open doc's bookmark", async ({ page }) => {
  await setup(page);
  await page.goto(
    `/admin/docs?project=${PROJECT_A}&path=${encodeURIComponent("docs/01-overview/vision.md")}`,
    { waitUntil: "domcontentloaded" },
  );

  await expect(page.getByTestId("docs-selected-path")).toHaveText("docs/01-overview/vision.md");
  const star = page.getByTestId("docs-viewer-star");
  await expect(star).toHaveAttribute("aria-pressed", "false");

  await star.click();
  await expect(star).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("docs-bookmarks-count")).toHaveText("1");
});

test("the recency filter narrows the bookmarks list", async ({ page }) => {
  await setup(page);
  await openTree(page);

  await page.getByTestId(`docs-tree-star-${PROJECT_A}-docs/README.md`).click();
  await page.getByTestId("docs-rail-tab-bookmarks").click();

  // Just-starred → visible under "Hoy".
  await page.getByTestId("docs-bookmarks-recency-1").click();
  await expect(page.getByTestId(`docs-bookmark-${PROJECT_A}-docs/README.md`)).toBeVisible();

  // "Todos" keeps it visible too.
  await page.getByTestId("docs-bookmarks-recency-all").click();
  await expect(page.getByTestId(`docs-bookmark-${PROJECT_A}-docs/README.md`)).toBeVisible();
});
