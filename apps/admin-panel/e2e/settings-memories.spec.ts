import { expect, test, type Page } from "@playwright/test";

/**
 * E2E for /admin/settings/memories (Plan 06.7 task_06_7_07).
 */

const REGISTRY_FIXTURE = {
  categories: {
    memories: {
      label_es: "Memorias",
      icon: "Brain",
      description_es: "...",
      external_page: null,
      settings: {
        "similarity.threshold": {
          type: "float",
          default: 0.85,
          label_es: "Umbral de similitud",
          description_es: "...",
          min_value: 0.5,
          max_value: 0.99,
        },
        "similarity.limit": {
          type: "int",
          default: 5,
          label_es: "Número de candidatos",
          description_es: "...",
          min_value: 1,
          max_value: 20,
        },
      },
    },
  },
};

const VALUES_FIXTURE = [
  {
    category: "memories",
    key: "similarity.threshold",
    value: 0.85,
    is_default: true,
  },
  {
    category: "memories",
    key: "similarity.limit",
    value: 5,
    is_default: true,
  },
];

async function setup(
  page: Page,
  opts: { onPut?: (key: string, body: object) => void } = {},
): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/tenant-settings/_registry", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(REGISTRY_FIXTURE),
    }),
  );
  await page.route("**/tenant-settings/memories", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(VALUES_FIXTURE),
    }),
  );
  await page.route("**/tenant-settings/memories/*", async (route) => {
    if (route.request().method() !== "PUT") return route.fallback();
    const url = new URL(route.request().url());
    const key = url.pathname.split("/").pop() ?? "";
    const body = JSON.parse(route.request().postData() ?? "{}");
    opts.onPut?.(key, body);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        category: "memories",
        key,
        value: body.value,
        is_default: false,
      }),
    });
  });
}

test("renders threshold + limit form with values from server", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings/memories", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("settings-memories-page")).toBeVisible();
  await expect(page.getByTestId("settings-memories-threshold")).toHaveValue("0.85");
  await expect(page.getByTestId("settings-memories-limit")).toHaveValue("5");
});

test("save sends a PUT per changed setting", async ({ page }) => {
  const puts: Array<{ key: string; body: object }> = [];
  await setup(page, {
    onPut: (key, body) => puts.push({ key, body }),
  });
  await page.goto("/admin/settings/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("settings-memories-limit").fill("8");
  await page.getByTestId("settings-memories-save").click();

  await page.waitForTimeout(300);
  // Two PUTs (threshold + limit). The limit should carry the new value.
  expect(puts.length).toBe(2);
  const limitPut = puts.find((p) => p.key === "similarity.limit");
  expect(limitPut).toBeTruthy();
  expect(limitPut!.body).toMatchObject({ value: 8 });
});

test("status updates after successful save", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings/memories", { waitUntil: "domcontentloaded" });

  await page.getByTestId("settings-memories-save").click();
  await expect(page.getByTestId("settings-memories-status")).toContainText(/Guardado/);
});
