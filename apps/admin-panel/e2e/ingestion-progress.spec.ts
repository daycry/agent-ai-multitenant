import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/documents/{id}/ingestion (Plan 04 task_04_15).
 *
 * Stubs the WebSocket `/ws/documents/{id}` server-side. Drives:
 *   - the page renders with the empty state,
 *   - a `document.status` event flips the status badge,
 *   - a `document.progress` event appends an entry to the list.
 */

const DOCUMENT_ID = "deadbeef-0000-0000-0000-000000000001";

async function setup(page: Page): Promise<void> {
  await seedSession(page);
  await page.addInitScript(() => {
    // Patch WebSocket so the page connects to an in-process fake
    // we drive from the test. Production wiring (browser →
    // ws://localhost:8001/ws/documents/{id}) needs the api-server
    // running; we don't have it in Playwright.
    const originalWS = window.WebSocket;
    class FakeWS extends EventTarget {
      static instances: FakeWS[] = [];
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = 0;
      url: string;
      onmessage: ((ev: MessageEvent) => void) | null = null;
      onopen: ((ev: Event) => void) | null = null;
      onerror: ((ev: Event) => void) | null = null;
      onclose: ((ev: CloseEvent) => void) | null = null;
      constructor(url: string) {
        super();
        this.url = url;
        FakeWS.instances.push(this);
        setTimeout(() => {
          this.readyState = 1;
          this.onopen?.(new Event("open"));
        }, 0);
      }
      send(): void {}
      close(): void {
        this.readyState = 3;
      }
      emit(payload: unknown): void {
        const ev = new MessageEvent("message", { data: JSON.stringify(payload) });
        this.onmessage?.(ev);
      }
    }
    (window as unknown as { WebSocket: typeof originalWS }).WebSocket =
      FakeWS as unknown as typeof originalWS;
    (window as unknown as { __fakeWsInstances: FakeWS[] }).__fakeWsInstances = FakeWS.instances;
  });
}

async function emitWsFrame(page: Page, frame: Record<string, unknown>): Promise<void> {
  // The page builds the WebSocket inside a useEffect — wait until at
  // least one instance exists before pushing a frame.
  await page.waitForFunction(() => {
    const wins = (window as unknown as { __fakeWsInstances?: { emit: (p: unknown) => void }[] })
      .__fakeWsInstances;
    return Array.isArray(wins) && wins.length > 0;
  });
  await page.evaluate((f) => {
    const wins = (window as unknown as { __fakeWsInstances: { emit: (p: unknown) => void }[] })
      .__fakeWsInstances;
    wins[wins.length - 1].emit(f);
  }, frame);
}

test("page renders with an empty events list", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/ingestion`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("ingestion-page")).toBeVisible();
  await expect(page.getByTestId("ingestion-events-empty")).toBeVisible();
  await expect(page.getByTestId("ingestion-status-badge")).toHaveAttribute(
    "data-status",
    "pending",
  );
});

test("a status event flips the badge and appends to the list", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/ingestion`, {
    waitUntil: "domcontentloaded",
  });

  await emitWsFrame(page, {
    type: "document.status",
    occurred_at: "2026-05-25T12:00:00Z",
    payload: JSON.stringify({
      document_id: DOCUMENT_ID,
      kb_id: "kb-1",
      status: "processing",
    }),
  });

  await expect(page.getByTestId("ingestion-status-badge")).toHaveAttribute(
    "data-status",
    "processing",
  );
  await expect(page.getByTestId("ingestion-event-0")).toBeVisible();
  await expect(page.getByTestId("ingestion-event-0")).toHaveAttribute("data-event-kind", "status");
});

test("a progress event appends an entry below the status one", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/ingestion`, {
    waitUntil: "domcontentloaded",
  });

  await emitWsFrame(page, {
    type: "document.status",
    occurred_at: "2026-05-25T12:00:00Z",
    payload: JSON.stringify({ status: "processing" }),
  });
  await emitWsFrame(page, {
    type: "document.progress",
    occurred_at: "2026-05-25T12:00:01Z",
    payload: JSON.stringify({ stage: "chunked", detail: "12 chunks" }),
  });

  const progressEvent = page.getByTestId("ingestion-event-1");
  await expect(progressEvent).toBeVisible();
  await expect(progressEvent).toHaveAttribute("data-event-kind", "progress");
  await expect(progressEvent).toContainText("chunked");
  await expect(progressEvent).toContainText("12 chunks");
});

test("a final status indexed flips the badge to success", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/documents/${DOCUMENT_ID}/ingestion`, {
    waitUntil: "domcontentloaded",
  });

  await emitWsFrame(page, {
    type: "document.status",
    occurred_at: "2026-05-25T12:01:00Z",
    payload: JSON.stringify({ status: "indexed", chunks: 7 }),
  });
  await expect(page.getByTestId("ingestion-status-badge")).toHaveAttribute(
    "data-status",
    "indexed",
  );
  await expect(page.getByTestId("ingestion-event-0")).toContainText("7 chunks");
});
