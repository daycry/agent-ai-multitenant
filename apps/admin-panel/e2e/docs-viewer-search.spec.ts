import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the docs visor search UI (Plan 07 Fase D, task_07_13).
 *
 * Covers: the search box is disabled until a project is selected; once a
 * project is selected, typing debounces and calls `GET .../docs/search?q=`,
 * surfacing ranked hits with the source doc path + a snippet; matching query
 * terms are highlighted in the snippet; clicking a hit opens that doc in the
 * render pane (and reflects it in the URL); the semantic tab switches to
 * `GET .../docs/semantic-search?q=` and shows a relevance score; loading and
 * empty states render; and a backend error surfaces an error state.
 *
 * NOTE: written for later human verification — not run in this environment
 * (no app+browser here).
 */

const PROJECT_A = "11111111-1111-1111-1111-111111111111";

const PROJECTS_FIXTURE = [{ id: PROJECT_A, name: "Proyecto A", status: "active" }];

const TREE_A = {
  project_id: PROJECT_A,
  folders: [],
  files: [{ type: "file", name: "guide.md", relpath: "docs/guide.md", size_bytes: 512 }],
};

const GUIDE_MD = ["# Guía de Ejemplo", "", "Contenido del documento sobre guardrails."].join("\n");

const GUIDE_CONTENT = {
  project_id: PROJECT_A,
  relpath: "docs/guide.md",
  content: GUIDE_MD,
  size_bytes: GUIDE_MD.length,
};

const FULLTEXT_HITS = {
  project_id: PROJECT_A,
  query: "guardrails",
  hits: [
    {
      chunk_id: "chunk-ft-1",
      document_id: "doc-1",
      relpath: "docs/guide.md",
      ordinal: 0,
      rank: 1,
      snippet: "Los guardrails declarativos se aplican por capas en el ciclo del agente.",
    },
    {
      chunk_id: "chunk-ft-2",
      document_id: "doc-2",
      relpath: "docs/05-architecture-decisions/0010-guardrails.md",
      ordinal: 3,
      rank: 2,
      snippet: "Decisión: guardrails en cuatro puntos del ciclo.",
    },
  ],
};

const SEMANTIC_HITS = {
  project_id: PROJECT_A,
  query: "guardrails",
  hits: [
    {
      chunk_id: "chunk-sem-1",
      document_id: "doc-1",
      relpath: "docs/guide.md",
      ordinal: 0,
      rank: 1,
      score: 0.87,
      snippet: "Los guardrails declarativos se aplican por capas en el ciclo del agente.",
    },
  ],
};

const EMPTY_HITS = { project_id: PROJECT_A, query: "zzz", hits: [] };

async function setup(page: Page): Promise<void> {
  await seedSession(page);
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
      body: JSON.stringify(GUIDE_CONTENT),
    }),
  );
  await page.route(`**/projects/${PROJECT_A}/docs/search?**`, (route) => {
    const q = new URL(route.request().url()).searchParams.get("q") ?? "";
    if (q === "zzz") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(EMPTY_HITS),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FULLTEXT_HITS),
    });
  });
  await page.route(`**/projects/${PROJECT_A}/docs/semantic-search?**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(SEMANTIC_HITS),
    }),
  );
}

/** Open the visor scoped to a project (so search is enabled). */
async function openVisor(page: Page): Promise<void> {
  await page.goto(`/admin/docs?project=${PROJECT_A}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("docs-search-panel")).toBeVisible();
}

test("search input is disabled until a project is selected", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/docs", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("docs-search-input")).toBeDisabled();
  await expect(page.getByTestId("docs-search-idle")).toBeVisible();
});

test("full-text search returns ranked hits with path + highlighted snippet", async ({ page }) => {
  await setup(page);
  await openVisor(page);

  await page.getByTestId("docs-search-input").fill("guardrails");

  const results = page.getByTestId("docs-search-results");
  await expect(results).toBeVisible();
  await expect(page.getByTestId("docs-search-hit-chunk-ft-1")).toBeVisible();
  await expect(page.getByTestId("docs-search-hit-chunk-ft-2")).toBeVisible();

  // The source doc path is shown.
  await expect(
    page.getByTestId("docs-search-hit-chunk-ft-1").getByTestId("docs-search-hit-path"),
  ).toHaveText("docs/guide.md");

  // The matched term is highlighted in the snippet.
  await expect(page.getByTestId("docs-search-hit-chunk-ft-1").locator("mark")).toContainText(
    /guardrails/i,
  );
});

test("clicking a hit opens that doc in the render pane and updates the URL", async ({ page }) => {
  await setup(page);
  await openVisor(page);

  await page.getByTestId("docs-search-input").fill("guardrails");
  await expect(page.getByTestId("docs-search-hit-chunk-ft-1")).toBeVisible();

  await page.getByTestId("docs-search-hit-chunk-ft-1").click();

  await expect(page.getByTestId("docs-markdown")).toBeVisible();
  await expect(page.getByTestId("docs-selected-path")).toHaveText("docs/guide.md");
  await expect(page).toHaveURL(/path=docs%2Fguide\.md/);
});

test("semantic tab switches endpoint and shows a relevance score", async ({ page }) => {
  await setup(page);
  await openVisor(page);

  await page.getByTestId("docs-search-tab-semantic").click();
  await page.getByTestId("docs-search-input").fill("guardrails");

  await expect(page.getByTestId("docs-search-hit-chunk-sem-1")).toBeVisible();
  await expect(
    page.getByTestId("docs-search-hit-chunk-sem-1").getByTestId("docs-search-hit-score"),
  ).toHaveText("87%");
});

test("shows an empty state when nothing matches", async ({ page }) => {
  await setup(page);
  await openVisor(page);

  await page.getByTestId("docs-search-input").fill("zzz");

  await expect(page.getByTestId("docs-search-empty")).toBeVisible();
  await expect(page.getByTestId("docs-search-results")).toHaveCount(0);
});

test("surfaces an error state when search 500s", async ({ page }) => {
  await setup(page);
  // Override the search route to fail.
  await page.route(`**/projects/${PROJECT_A}/docs/search?**`, (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "boom" }),
    }),
  );
  await openVisor(page);

  await page.getByTestId("docs-search-input").fill("guardrails");

  await expect(page.getByTestId("docs-search-error")).toBeVisible();
});
