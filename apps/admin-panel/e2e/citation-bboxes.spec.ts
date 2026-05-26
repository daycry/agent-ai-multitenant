import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for /admin/documents/{id}/citations (Plan 04 task_04_25).
 *
 * Verifies the citation viewer surface:
 *   - one page placeholder per page of the document,
 *   - one bbox overlay per chunk with bbox, positioned in normalised
 *     coords,
 *   - the sidebar lists every chunk,
 *   - clicking a sidebar item marks the corresponding bbox active.
 */

const DOCUMENT_ID = "ffff0000-0000-0000-0000-000000000001";

const PAYLOAD = {
  document: {
    id: DOCUMENT_ID,
    kb_id: "aaaa0000-0000-0000-0000-000000000001",
    title: "Manual onboarding v3",
    source_filename: "onboarding.pdf",
    source_mime_type: "application/pdf",
    page_count: 3,
    status: "indexed",
  },
  chunks: [
    {
      id: "c-0",
      ordinal: 0,
      content: "Introducción a la plataforma agéntica.",
      bbox: { page: 0, x: 0.1, y: 0.1, w: 0.8, h: 0.05 },
      metadata: { heading: "Intro" },
    },
    {
      id: "c-1",
      ordinal: 1,
      content: "Convención de cómo trabajamos con Knowledge Bases.",
      bbox: { page: 0, x: 0.1, y: 0.25, w: 0.8, h: 0.05 },
      metadata: {},
    },
    {
      id: "c-2",
      ordinal: 2,
      content: "Tabla con los modelos LLM soportados (ADR 0021).",
      bbox: { page: 1, x: 0.05, y: 0.3, w: 0.9, h: 0.4 },
      metadata: { kind: "table" },
    },
    {
      id: "c-3",
      ordinal: 3,
      content: "Glosario sin posición — bbox nulo intencional.",
      bbox: null,
      metadata: {},
    },
  ],
};

async function setup(page: Page, payload = PAYLOAD): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route(`http://localhost:8001/documents/${DOCUMENT_ID}/citations`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    }),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
test("page renders one placeholder per page of the document", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/citations`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("citations-page")).toBeVisible();
  await expect(page.getByTestId("citations-page-0")).toBeVisible();
  await expect(page.getByTestId("citations-page-1")).toBeVisible();
  await expect(page.getByTestId("citations-page-2")).toBeVisible();
});

test("each chunk with a bbox renders one overlay positioned in normalised coords", async ({
  page,
}) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/citations`, {
    waitUntil: "domcontentloaded",
  });

  // Two overlays on page 0, one on page 1.
  await expect(page.getByTestId("citations-bbox-c-0")).toBeVisible();
  await expect(page.getByTestId("citations-bbox-c-1")).toBeVisible();
  await expect(page.getByTestId("citations-bbox-c-2")).toBeVisible();
  // c-3 has bbox=null → no overlay.
  await expect(page.getByTestId("citations-bbox-c-3")).toHaveCount(0);

  // Inline-style coords are the normalised % strings.
  const bbox0Style = await page.getByTestId("citations-bbox-c-0").getAttribute("style");
  expect(bbox0Style).toContain("left: 10%");
  expect(bbox0Style).toContain("top: 10%");
  expect(bbox0Style).toContain("width: 80%");
  expect(bbox0Style).toContain("height: 5%");
});

test("sidebar lists every chunk regardless of bbox presence", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/citations`, {
    waitUntil: "domcontentloaded",
  });
  for (const id of ["c-0", "c-1", "c-2", "c-3"]) {
    await expect(page.getByTestId(`citations-sidebar-item-${id}`)).toBeVisible();
  }
});

test("clicking a sidebar item marks the corresponding bbox active", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/citations`, {
    waitUntil: "domcontentloaded",
  });

  // No active by default.
  await expect(page.getByTestId("citations-bbox-c-1")).toHaveAttribute("data-active", "false");

  await page.getByTestId("citations-sidebar-item-c-1").click();

  await expect(page.getByTestId("citations-bbox-c-1")).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("citations-sidebar-item-c-1")).toHaveAttribute(
    "data-active",
    "true",
  );
  // Other bboxes stay inactive.
  await expect(page.getByTestId("citations-bbox-c-0")).toHaveAttribute("data-active", "false");
});

test("document with zero chunks shows the empty viewer state", async ({ page }) => {
  const empty = {
    document: { ...PAYLOAD.document, page_count: 0 },
    chunks: [],
  };
  await setup(page, empty);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/citations`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("citations-viewer-empty")).toBeVisible();
  await expect(page.getByTestId("citations-sidebar-empty")).toBeVisible();
});
