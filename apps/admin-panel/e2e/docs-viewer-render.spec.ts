import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for the docs visor markdown rendering pane (Plan 07 Fase D, task_07_12).
 *
 * Covers: selecting a file fetches `/content` and renders it with
 * react-markdown (GFM heading + table + code highlight), a ```mermaid fence
 * becomes a rendered diagram (or its loading state), an auto-generated table
 * of contents is built from the headings, raw HTML in the source is NOT
 * injected (safe render), and a 404 from the content endpoint surfaces an
 * error state.
 *
 * NOTE: written for later human verification — not run in this environment
 * (no app+browser here).
 */

const PROJECT_A = "11111111-1111-1111-1111-111111111111";

const PROJECTS_FIXTURE = [{ id: PROJECT_A, name: "Proyecto A", status: "active" }];

const TREE_A = {
  project_id: PROJECT_A,
  folders: [],
  files: [
    { type: "file", name: "guide.md", relpath: "docs/guide.md", size_bytes: 512 },
    { type: "file", name: "missing.md", relpath: "docs/missing.md", size_bytes: 0 },
    { type: "file", name: "unsafe.md", relpath: "docs/unsafe.md", size_bytes: 64 },
  ],
};

// A doc exercising headings (→ TOC), a GFM table, a fenced code block and a
// mermaid diagram.
const GUIDE_MD = [
  "# Guía de Ejemplo",
  "",
  "Texto introductorio del documento.",
  "",
  "## Primera Sección",
  "",
  "Un párrafo con `código` en línea.",
  "",
  "```python",
  "def hello():",
  '    return "world"',
  "```",
  "",
  "## Diagrama",
  "",
  "```mermaid",
  "graph TD;",
  "  A-->B;",
  "```",
  "",
  "## Tabla",
  "",
  "| Col 1 | Col 2 |",
  "| ----- | ----- |",
  "| a     | b     |",
  "",
].join("\n");

const GUIDE_CONTENT = {
  project_id: PROJECT_A,
  relpath: "docs/guide.md",
  content: GUIDE_MD,
  size_bytes: GUIDE_MD.length,
};

// A doc whose source contains raw HTML — the renderer must show it as text,
// never inject a live element.
const UNSAFE_MD = [
  "# Documento",
  "",
  "<script>window.__pwned = true;</script>",
  "",
  '<img src=x onerror="window.__pwned = true" />',
  "",
  "Texto normal.",
].join("\n");

const UNSAFE_CONTENT = {
  project_id: PROJECT_A,
  relpath: "docs/unsafe.md",
  content: UNSAFE_MD,
  size_bytes: UNSAFE_MD.length,
};

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
  await page.route(`**/projects/${PROJECT_A}/docs/content?**`, (route) => {
    const url = new URL(route.request().url());
    const path = url.searchParams.get("path");
    if (path === "docs/guide.md") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(GUIDE_CONTENT),
      });
    }
    if (path === "docs/unsafe.md") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(UNSAFE_CONTENT),
      });
    }
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "doc not found" }),
    });
  });
}

async function openDoc(page: Page, relpath: string): Promise<void> {
  await page.goto(`/admin/docs?project=${PROJECT_A}&path=${encodeURIComponent(relpath)}`, {
    waitUntil: "domcontentloaded",
  });
}

test("renders markdown: heading, inline code, highlighted code block, table", async ({ page }) => {
  await setup(page);
  await openDoc(page, "docs/guide.md");

  const md = page.getByTestId("docs-markdown");
  await expect(md).toBeVisible();

  // Heading rendered as a real <h1> with the slugged id rehype-slug assigns.
  await expect(md.getByRole("heading", { level: 1, name: "Guía de Ejemplo" })).toBeVisible();
  // OJO con el slug: `github-slugger` —la librería que usa `rehype-slug`, y la
  // misma que `extractToc`— CONSERVA los acentos. "Primera Sección" es
  // `#primera-sección`, no `#primera-seccion`; el spec daba por hecho que se
  // limpiaban y buscaba un ancla que no existe (2026-08-19).
  await expect(md.locator("#primera-sección")).toBeVisible();

  // GFM table rendered.
  await expect(md.getByRole("table")).toBeVisible();
  await expect(md.getByRole("cell", { name: "a" })).toBeVisible();

  // Code block highlighted (rehype-highlight emits .hljs).
  await expect(md.locator("code.hljs")).toHaveCount(1);
});

test("auto-generates a table of contents from the headings", async ({ page }) => {
  await setup(page);
  await openDoc(page, "docs/guide.md");

  const toc = page.getByTestId("docs-toc");
  await expect(toc).toBeVisible();
  await expect(page.getByTestId("docs-toc-link-primera-sección")).toBeVisible();
  await expect(page.getByTestId("docs-toc-link-diagrama")).toBeVisible();
  await expect(page.getByTestId("docs-toc-link-tabla")).toBeVisible();
  // The link points at the matching heading anchor.
  await expect(page.getByTestId("docs-toc-link-tabla")).toHaveAttribute("href", "#tabla");
  // Y el caso que de verdad puede desalinearse: con acento, el href del TOC y el
  // id que pinta el renderer tienen que seguir siendo el MISMO slug.
  await expect(page.getByTestId("docs-toc-link-primera-sección")).toHaveAttribute(
    "href",
    "#primera-sección",
  );
  await expect(page.getByTestId("docs-markdown").locator("#primera-sección")).toBeVisible();
});

test("renders a mermaid fence as a diagram (not a raw code block)", async ({ page }) => {
  await setup(page);
  await openDoc(page, "docs/guide.md");

  // Either the diagram resolved to SVG, or it's still in its loading state —
  // both prove the fence was routed to the mermaid component, not <pre><code>.
  const diagram = page.getByTestId("docs-mermaid");
  const loading = page.getByTestId("docs-mermaid-loading");
  await expect(diagram.or(loading)).toBeVisible();
});

test("does not inject raw HTML from the source (safe render)", async ({ page }) => {
  await setup(page);
  await openDoc(page, "docs/unsafe.md");

  await expect(page.getByTestId("docs-markdown")).toBeVisible();
  // skipHtml means the <script>/<img onerror> never become live nodes.
  await expect(page.locator("img[onerror]")).toHaveCount(0);
  // The malicious payload never ran.
  const pwned = await page.evaluate(() => (window as unknown as { __pwned?: boolean }).__pwned);
  expect(pwned).toBeFalsy();
});

test("shows an error state when the content endpoint 404s", async ({ page }) => {
  await setup(page);
  await openDoc(page, "docs/missing.md");

  await expect(page.getByTestId("docs-content-error")).toBeVisible();
  await expect(page.getByTestId("docs-markdown")).toHaveCount(0);
});

test("shows the empty state when no document is selected", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/docs", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("docs-content-empty")).toBeVisible();
});
