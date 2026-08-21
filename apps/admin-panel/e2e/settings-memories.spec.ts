import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E for /admin/settings/memories (Plan 06.7 task_06_7_07).
 *
 * Reparado el 2026-08-19: los dos tests que ESCRIBEN en el formulario corrían
 * una carrera contra la hidratación. La pantalla arranca con los defaults del
 * componente (0.85 / 5) y un `useEffect` los sobrescribe con lo que devuelve
 * `GET /tenant-settings/memories`. Si el spec teclea antes de que ese efecto
 * corra, el valor tecleado se pierde y el PUT viaja con el valor viejo (visto en
 * local: `value: 5` donde se esperaba `8`); si además teclea antes de que React
 * hidrate, el click no dispara nada y ni siquiera aparece el `-status`, que es
 * como se veía en CI.
 *
 * La espera no es un `waitForTimeout`: se afirma que los valores del SERVIDOR ya
 * están en el DOM. Eso ancla el resto del test a un estado conocido y, de paso,
 * comprueba la hidratación en vez de suponerla.
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

/**
 * Valores DISTINTOS de los defaults del componente (0.85 / 5) a propósito.
 *
 * Con los defaults, `toHaveValue("5")` pasaba en el primer render — antes de que
 * la respuesta del servidor existiera —, así que ni el test que dice "renderiza
 * los valores del servidor" comprobaba nada, ni servía como espera de
 * hidratación. Con 0.90 / 7 sólo pasa si el efecto de hidratación ya corrió.
 */
const VALUES_FIXTURE = [
  {
    category: "memories",
    key: "similarity.threshold",
    value: 0.9,
    is_default: false,
  },
  {
    category: "memories",
    key: "similarity.limit",
    value: 7,
    is_default: false,
  },
];

async function setup(
  page: Page,
  opts: { onPut?: (key: string, body: object) => void } = {},
): Promise<void> {
  await seedSession(page);
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
  await expect(page.getByTestId("settings-memories-threshold")).toHaveValue("0.9");
  await expect(page.getByTestId("settings-memories-limit")).toHaveValue("7");
});

test("save sends a PUT per changed setting", async ({ page }) => {
  const puts: Array<{ key: string; body: object }> = [];
  await setup(page, {
    onPut: (key, body) => puts.push({ key, body }),
  });
  await page.goto("/admin/settings/memories", { waitUntil: "domcontentloaded" });

  // El formulario ya lleva los valores del servidor: a partir de aquí, lo que
  // se teclee no lo pisa la hidratación.
  await expect(page.getByTestId("settings-memories-limit")).toHaveValue("7");
  await page.getByTestId("settings-memories-limit").fill("8");
  await page.getByTestId("settings-memories-save").click();

  await page.waitForTimeout(300);
  // Two PUTs (threshold + limit). The limit should carry the new value.
  expect(puts.length).toBe(2);
  const limitPut = puts.find((p) => p.key === "similarity.limit");
  expect(limitPut).toBeTruthy();
  expect(limitPut!.body).toMatchObject({ value: 8 });
  // El ajuste que NO se tocó viaja con el valor del SERVIDOR, no con el default
  // del componente: si la hidratación se perdiera, aquí saldría 0.85.
  const thresholdPut = puts.find((p) => p.key === "similarity.threshold");
  expect(thresholdPut!.body).toMatchObject({ value: 0.9 });
});

test("status updates after successful save", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/settings/memories", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("settings-memories-limit")).toHaveValue("7");
  await page.getByTestId("settings-memories-save").click();
  await expect(page.getByTestId("settings-memories-status")).toContainText(/Guardado/);
});
