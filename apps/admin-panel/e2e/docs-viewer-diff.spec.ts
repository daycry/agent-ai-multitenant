import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for the docs visor version-diff view (Plan 07 Fase D, task_07_16).
 *
 * Covers: switching the viewer to "Comparar", entering two git refs and
 * comparing fetches `/diff` and renders the classified lines with added /
 * removed styling + the add/remove counts; an empty diff shows the "no
 * differences" state; a bad-ref 400 surfaces the API error; and switching back
 * to "Documento" restores the markdown render.
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

const GUIDE_MD = ["# Guía", "", "Contenido del documento."].join("\n");

const GUIDE_CONTENT = {
  project_id: PROJECT_A,
  relpath: "docs/guide.md",
  content: GUIDE_MD,
  size_bytes: GUIDE_MD.length,
};

// A diff with one added line, one removed line, one context line and a hunk
// header — exercises every `kind` the renderer styles.
const DIFF_CHANGED = {
  project_id: PROJECT_A,
  relpath: "docs/guide.md",
  base_ref: "HEAD~1",
  head_ref: "HEAD",
  unchanged: false,
  added: 1,
  removed: 1,
  raw: "@@ -1,2 +1,2 @@\n # Guía\n-Antiguo contenido.\n+Contenido del documento.",
  lines: [
    { kind: "hunk", content: "@@ -1,2 +1,2 @@" },
    { kind: "context", content: "# Guía" },
    { kind: "removed", content: "Antiguo contenido." },
    { kind: "added", content: "Contenido del documento." },
  ],
};

const DIFF_UNCHANGED = {
  project_id: PROJECT_A,
  relpath: "docs/guide.md",
  base_ref: "v1",
  head_ref: "v1",
  unchanged: true,
  added: 0,
  removed: 0,
  raw: "",
  lines: [],
};

async function setup(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
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
  await page.route(`**/projects/${PROJECT_A}/docs/diff?**`, (route) => {
    const url = new URL(route.request().url());
    const base = url.searchParams.get("base");
    const head = url.searchParams.get("head");
    if (base === "bad-ref" || head === "bad-ref") {
      return route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "could not diff the given refs" }),
      });
    }
    if (base === head) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(DIFF_UNCHANGED),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DIFF_CHANGED),
    });
  });
}

async function openDoc(page: Page): Promise<void> {
  await page.goto(`/admin/docs?project=${PROJECT_A}&path=${encodeURIComponent("docs/guide.md")}`, {
    waitUntil: "domcontentloaded",
  });
}

test("compares two refs and renders the classified diff with counts", async ({ page }) => {
  await setup(page);
  await openDoc(page);

  // Switch to the comparison mode.
  await page.getByTestId("docs-viewer-mode-diff").click();
  await expect(page.getByTestId("docs-diff-view")).toBeVisible();
  // Idle until the user compares.
  await expect(page.getByTestId("docs-diff-idle")).toBeVisible();

  // Defaults are HEAD~1 → HEAD; just compare.
  await page.getByTestId("docs-diff-submit").click();

  const result = page.getByTestId("docs-diff-result");
  await expect(result).toBeVisible();

  // Add / remove counts from the API summary.
  await expect(page.getByTestId("docs-diff-added-count")).toContainText("+1");
  await expect(page.getByTestId("docs-diff-removed-count")).toContainText("-1");

  // Classified lines styled per kind.
  await expect(page.getByTestId("docs-diff-line-added")).toContainText("Contenido del documento.");
  await expect(page.getByTestId("docs-diff-line-removed")).toContainText("Antiguo contenido.");
  await expect(page.locator('[data-diff-kind="hunk"]')).toBeVisible();
});

test("shows the no-differences state for an empty diff", async ({ page }) => {
  await setup(page);
  await openDoc(page);

  await page.getByTestId("docs-viewer-mode-diff").click();
  await page.getByTestId("docs-diff-base-input").fill("v1");
  await page.getByTestId("docs-diff-head-input").fill("v1");
  await page.getByTestId("docs-diff-submit").click();

  await expect(page.getByTestId("docs-diff-unchanged")).toBeVisible();
  await expect(page.getByTestId("docs-diff-result")).toHaveCount(0);
});

test("surfaces an error state when the diff endpoint 400s", async ({ page }) => {
  await setup(page);
  await openDoc(page);

  await page.getByTestId("docs-viewer-mode-diff").click();
  await page.getByTestId("docs-diff-base-input").fill("bad-ref");
  await page.getByTestId("docs-diff-head-input").fill("HEAD");
  await page.getByTestId("docs-diff-submit").click();

  await expect(page.getByTestId("docs-diff-error")).toBeVisible();
});

test("returns to the markdown render when switching back to Documento", async ({ page }) => {
  await setup(page);
  await openDoc(page);

  await page.getByTestId("docs-viewer-mode-diff").click();
  await expect(page.getByTestId("docs-diff-view")).toBeVisible();

  await page.getByTestId("docs-viewer-mode-read").click();
  await expect(page.getByTestId("docs-markdown")).toBeVisible();
  await expect(page.getByTestId("docs-diff-view")).toHaveCount(0);
});
