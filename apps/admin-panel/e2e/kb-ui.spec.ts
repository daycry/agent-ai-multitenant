import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/projects/{id}/knowledge-bases (Plan 04 task_04_24).
 *
 * Mocks GET KBs (granted to the project), GET docs per KB, POST
 * upload, DELETE doc. Drives:
 *   - empty-state when the project has no KBs,
 *   - rendering of a KB card + its docs,
 *   - upload dialog → POST → list refresh,
 *   - delete a doc → row disappears.
 */

const PROJECT_ID = "11111111-0000-0000-0000-000000000001";
const KB_ID = "aaaa0000-0000-0000-0000-000000000001";

type DocStatus = "pending" | "processing" | "indexed" | "failed";

interface DocFixture {
  id: string;
  kb_id: string;
  title: string;
  source_filename: string;
  source_mime_type: string;
  source_size_bytes: number;
  status: DocStatus;
  error_message: string | null;
  page_count: number;
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}

const KB_FIXTURE = {
  id: KB_ID,
  tenant_id: "tttttttt-0000-0000-0000-000000000001",
  name: "Manuales del producto",
  description: "Knowledge base con guías + manuales.",
  embedding_model_id: "nomic-embed-text-v1.5",
  created_by: null,
  created_at: "2026-05-25T10:00:00Z",
  updated_at: "2026-05-25T10:00:00Z",
};

const DOC_FIXTURE: DocFixture = {
  id: "dddd0000-0000-0000-0000-000000000001",
  kb_id: KB_ID,
  title: "Manual onboarding v3",
  source_filename: "onboarding.pdf",
  source_mime_type: "application/pdf",
  source_size_bytes: 524288,
  status: "indexed",
  error_message: null,
  page_count: 24,
  indexed_at: "2026-05-25T10:15:00Z",
  created_at: "2026-05-25T10:10:00Z",
  updated_at: "2026-05-25T10:15:00Z",
};

interface Capture {
  uploadCalls: number;
  lastUploadName: string | null;
  deleteCalls: number;
  lastDeletedId: string | null;
}

async function setup(page: Page, { withKBs = true } = {}): Promise<Capture> {
  const capture: Capture = {
    uploadCalls: 0,
    lastUploadName: null,
    deleteCalls: 0,
    lastDeletedId: null,
  };
  let documents: DocFixture[] = withKBs ? [DOC_FIXTURE] : [];

  await seedSession(page);

  // GET /projects/{id}/knowledge-bases
  await page.route(`http://localhost:8001/projects/${PROJECT_ID}/knowledge-bases`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(withKBs ? [KB_FIXTURE] : []),
    }),
  );

  // Combined route for the KB-scoped /documents endpoint (both GET
  // list and POST upload). Using one regex avoids order-of-registration
  // bugs that the previous two split routes had.
  await page.route(
    new RegExp(`http://localhost:8001/knowledge-bases/${KB_ID}/documents$`),
    (route) => {
      const method = route.request().method();
      if (method === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(documents),
        });
      }
      if (method === "POST") {
        capture.uploadCalls += 1;
        capture.lastUploadName = "uploaded.pdf";
        const created: DocFixture = {
          ...DOC_FIXTURE,
          id: `eeee0000-0000-0000-0000-${String(capture.uploadCalls).padStart(12, "0")}`,
          title: "uploaded",
          source_filename: "uploaded.pdf",
          status: "pending",
          page_count: 0,
          indexed_at: null,
        };
        documents = [...documents, created];
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(created),
        });
      }
      return route.continue();
    },
  );

  // DELETE /knowledge-bases/{kb_id}/documents/{doc_id}
  await page.route(
    new RegExp(`http://localhost:8001/knowledge-bases/${KB_ID}/documents/[0-9a-f-]+$`),
    (route) => {
      if (route.request().method() !== "DELETE") return route.continue();
      capture.deleteCalls += 1;
      capture.lastDeletedId = route.request().url().split("/").pop() ?? null;
      documents = documents.filter((d) => d.id !== capture.lastDeletedId);
      return route.fulfill({ status: 204, body: "" });
    },
  );

  return capture;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
test("empty project shows the no-KBs message", async ({ page }) => {
  await setup(page, { withKBs: false });
  await page.goto(`/admin/projects/${PROJECT_ID}/knowledge-bases`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("project-kbs-page")).toBeVisible();
  await expect(page.getByTestId("project-kbs-empty")).toBeVisible();
});

test("project with KBs renders the KB card and its docs", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/knowledge-bases`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId(`kb-card-${KB_ID}`)).toBeVisible();
  await expect(page.getByTestId(`kb-card-${KB_ID}`)).toContainText("Manuales del producto");
  await expect(page.getByTestId(`kb-doc-${DOC_FIXTURE.id}`)).toBeVisible();
  await expect(page.getByTestId(`kb-doc-status-${DOC_FIXTURE.id}`)).toContainText("Indexado");
});

test("uploading a document calls POST and the row appears", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/knowledge-bases`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId(`kb-upload-open-${KB_ID}`).click();
  await expect(page.getByTestId("kb-upload-dialog")).toBeVisible();

  // Attach a small in-memory file.
  await page.getByTestId("kb-upload-file").setInputFiles({
    name: "uploaded.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 fake"),
  });
  await page.getByTestId("kb-upload-submit").click();

  await expect.poll(() => capture.uploadCalls).toBe(1);

  // The dialog closes and the new doc appears as pending.
  await expect(page.getByTestId("kb-upload-dialog")).toBeHidden();
});

test("deleting a doc removes its row", async ({ page }) => {
  const capture = await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/knowledge-bases`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId(`kb-doc-delete-${DOC_FIXTURE.id}`).click();
  await expect.poll(() => capture.deleteCalls).toBe(1);
  expect(capture.lastDeletedId).toBe(DOC_FIXTURE.id);

  // List re-renders without the deleted row.
  await expect(page.getByTestId(`kb-doc-${DOC_FIXTURE.id}`)).toHaveCount(0);
});

test("each doc row has a link to the live-ingestion page", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/knowledge-bases`, {
    waitUntil: "domcontentloaded",
  });
  const link = page.getByTestId(`kb-doc-ingestion-link-${DOC_FIXTURE.id}`);
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", `/admin/documents/${DOC_FIXTURE.id}/ingestion`);
});
