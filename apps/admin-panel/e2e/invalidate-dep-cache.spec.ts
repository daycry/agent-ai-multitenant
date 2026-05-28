import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for /admin/projects/{id}/dep-cache (Plan 06 task_06_12).
 *
 * The page lists every runtime in the catalog. Clicking "Invalidar"
 * fires `POST /projects/{id}/dep-cache/invalidate` which we mock
 * here — the test pins the request body + response handling.
 */

const PROJECT_ID = "33333333-0000-0000-0000-000000000001";

async function setupAuth(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
}

async function mockInvalidate(
  page: Page,
  resolver: (req: { runtime: string; lock_hash?: string | null }) => {
    status?: number;
    body: Record<string, unknown>;
  },
): Promise<void> {
  await page.route(
    `http://localhost:8001/projects/${PROJECT_ID}/dep-cache/invalidate`,
    async (route) => {
      const req = JSON.parse(route.request().postData() ?? "{}");
      const result = resolver(req);
      await route.fulfill({
        status: result.status ?? 200,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
    },
  );
}

test("renders one row per runtime in the catalog", async ({ page }) => {
  await setupAuth(page);
  await page.goto(`/admin/projects/${PROJECT_ID}/dep-cache`, {
    waitUntil: "domcontentloaded",
  });
  await expect(page.getByTestId("dep-cache-table")).toBeVisible();

  // The catalog has 12 runtimes-with-lockfile (generic-shell and
  // generic-http skip the dep-cache).
  await expect(page.getByTestId("invalidate-python-pytest")).toBeVisible();
  await expect(page.getByTestId("invalidate-node-jest")).toBeVisible();
  await expect(page.getByTestId("invalidate-php-phpunit")).toBeVisible();
  await expect(page.getByTestId("invalidate-rust-cargo")).toBeVisible();
});

test("invalidate button calls the endpoint and shows the count", async ({ page }) => {
  await setupAuth(page);
  await mockInvalidate(page, (req) => ({
    body: {
      runtime: req.runtime,
      invalidated_count: 3,
      invalidated_paths: [
        "/data/agent-platform/dep-cache/pip-abc",
        "/data/agent-platform/dep-cache/pip-def",
        "/data/agent-platform/dep-cache/pip-ghi",
      ],
    },
  }));
  await page.goto(`/admin/projects/${PROJECT_ID}/dep-cache`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("invalidate-python-pytest").click();
  await expect(page.getByTestId("result-python-pytest")).toHaveText(/3 entradas invalidadas/);
});

test("server error surfaces in the result cell", async ({ page }) => {
  await setupAuth(page);
  await mockInvalidate(page, () => ({
    status: 422,
    body: { detail: "unknown runtime 'foo'; not in the catalog" },
  }));
  await page.goto(`/admin/projects/${PROJECT_ID}/dep-cache`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByTestId("invalidate-node-jest").click();
  await expect(page.getByTestId("result-node-jest")).toBeVisible();
  await expect(page.getByTestId("result-node-jest")).toHaveText(/unknown runtime/i);
});
