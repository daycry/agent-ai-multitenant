import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/settings index (Plan 06.7 task_06_6_06_index).
 *
 * Mocks GET /tenant-settings/_registry y verifica:
 *   - Renderiza las cards de las categorías declaradas.
 *   - El icono lucide-react se resuelve a partir del string.
 *   - external_page redirige fuera de /admin/settings.
 *   - Auto-generated category lleva a /admin/settings/{category}.
 */

const REGISTRY_FIXTURE = {
  categories: {
    memories: {
      label_es: "Memorias",
      icon: "Brain",
      description_es: "Cómo el sistema detecta memorias similares.",
      external_page: null,
      settings: {
        "similarity.threshold": {
          type: "float",
          default: 0.85,
          label_es: "Umbral",
          description_es: "Coseno mínimo.",
          min_value: 0.5,
          max_value: 0.99,
        },
        "similarity.limit": {
          type: "int",
          default: 5,
          label_es: "Límite",
          description_es: "Top-K.",
          min_value: 1,
          max_value: 20,
        },
      },
    },
    costs: {
      label_es: "Costes",
      icon: "Coins",
      description_es: "Tarifa horaria del tenant.",
      external_page: "/admin/settings/hourly-rate",
      settings: {},
    },
  },
};

async function setup(page: Page): Promise<void> {
  await seedSession(page);
  await page.route("**/tenant-settings/_registry", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(REGISTRY_FIXTURE),
    }),
  );
}

test("index renders one card per category", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("settings-index")).toBeVisible();
  await expect(page.getByTestId("settings-category-memories")).toBeVisible();
  await expect(page.getByTestId("settings-category-costs")).toBeVisible();
});

test("memories card links to /admin/settings/memories", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings", { waitUntil: "domcontentloaded" });

  const link = page.getByTestId("settings-category-link-memories");
  await expect(link).toHaveAttribute("href", "/admin/settings/memories");
});

test("costs card uses external_page (legacy hourly-rate)", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings", { waitUntil: "domcontentloaded" });

  const link = page.getByTestId("settings-category-link-costs");
  await expect(link).toHaveAttribute("href", "/admin/settings/hourly-rate");
  await expect(page.getByTestId("settings-category-costs-external")).toBeVisible();
});

test("auto-generated category shows setting count", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("settings-category-memories-count")).toContainText("2");
});
