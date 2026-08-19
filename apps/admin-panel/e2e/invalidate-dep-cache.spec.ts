import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/projects/{id}/dep-cache (Plan 06 task_06_12).
 *
 * The page lists every runtime in the catalog. Clicking "Invalidar"
 * fires `POST /projects/{id}/dep-cache/invalidate` which we mock
 * here — the test pins the request body + response handling.
 *
 * Reparado el 2026-08-19 (subset mockeado de CI): desde el Plan 06.18
 * (`task_06_18_11`) el catálogo de runtimes NO lo hardcodea el panel, lo sirve
 * `GET /runtime-templates` — precisamente porque las dos pantallas que lo
 * copiaban habían divergido (14 ids vs 12). El spec no mockeaba ese endpoint,
 * así que la query fallaba y la tabla no llegaba a pintarse: los tres tests
 * esperaban a un botón que no podía existir.
 */

const PROJECT_ID = "33333333-0000-0000-0000-000000000001";

/**
 * Catálogo servido. Incluye a propósito dos plantillas SIN dep-cache
 * (`dep_cache_mount: null`): la pantalla las filtra, y el primer test comprueba
 * que no aparecen — el filtro sin un caso negativo no se estaría probando.
 */
const RUNTIME_TEMPLATES = [
  {
    id: "python-pytest",
    label: { es: "Python · pytest", en: "Python · pytest" },
    dep_cache_mount: "/cache/pip",
    network_policy: "restricted",
  },
  {
    id: "node-jest",
    label: { es: "Node · Jest", en: "Node · Jest" },
    dep_cache_mount: "/cache/npm",
    network_policy: "restricted",
  },
  {
    id: "php-phpunit",
    label: { es: "PHP · PHPUnit", en: "PHP · PHPUnit" },
    dep_cache_mount: "/cache/composer",
    network_policy: "restricted",
  },
  {
    id: "rust-cargo",
    label: { es: "Rust · cargo", en: "Rust · cargo" },
    dep_cache_mount: "/cache/cargo",
    network_policy: "restricted",
  },
  {
    id: "generic-shell",
    label: { es: "Shell genérico", en: "Generic shell" },
    dep_cache_mount: null,
    network_policy: "none",
  },
  {
    id: "generic-http",
    label: { es: "HTTP genérico", en: "Generic HTTP" },
    dep_cache_mount: null,
    network_policy: "open",
  },
];

async function setupAuth(page: Page): Promise<void> {
  await seedSession(page);
  await page.route(apiRoute("/runtime-templates"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RUNTIME_TEMPLATES),
    }),
  );
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

  // Una fila por runtime CON dep-cache…
  await expect(page.getByTestId("invalidate-python-pytest")).toBeVisible();
  await expect(page.getByTestId("invalidate-node-jest")).toBeVisible();
  await expect(page.getByTestId("invalidate-php-phpunit")).toBeVisible();
  await expect(page.getByTestId("invalidate-rust-cargo")).toBeVisible();
  // …y ninguna para los que no lo tienen: no hay caché que invalidar.
  await expect(page.getByTestId("invalidate-generic-shell")).toHaveCount(0);
  await expect(page.getByTestId("invalidate-generic-http")).toHaveCount(0);
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
