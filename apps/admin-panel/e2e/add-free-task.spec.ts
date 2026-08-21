import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Añadir tarea libre al plan" form (Plan 06 task_06_34b5).
 *
 * La página de escaladas aloja el alta de tarea libre. Comprueba: 1) el diálogo
 * abre con sus campos, 2) enviar hace el POST con el cuerpo esperado y el
 * diálogo se cierra, 3) sin título no se puede enviar.
 *
 * Reparado el 2026-08-19 (subset mockeado de CI). Tres derivas, todas del panel
 * y ninguna cosmética:
 *
 *   1. **Los mocks apuntaban a `/api/plans/...`** y `lib/api.ts` pide
 *      `http://localhost:8001/plans/...`: no casaba ni una, así que la pantalla
 *      hablaba con un backend que no existe.
 *   2. **El formulario ya no está en la página**: vive en un `<Dialog>` que abre
 *      `free-task-open`. Los `free-task-*` existían pero dentro de un diálogo
 *      cerrado, y `toBeVisible()` no podía pasar nunca.
 *   3bis. Y una carrera de HIDRATACIÓN, que en CI se veía como "el diálogo no
 *      abre": el botón `free-task-open` existe en el HTML servido, así que
 *      Playwright puede pulsarlo ANTES de que React le cuelgue el handler, y el
 *      click se pierde en silencio. `openFreeTaskDialog()` espera primero a que
 *      la lista de escaladas haya pintado su estado vacío — eso sólo ocurre con
 *      el cliente ya vivo y la query resuelta.
 *
 *   3. **`free-task-status` no existe**: el éxito CIERRA el diálogo y refresca la
 *      lista, y el error se pinta en `free-task-error`. Y el caso "sin título"
 *      ya no muestra un mensaje: el botón está deshabilitado, que es una guarda
 *      más fuerte que un aviso a toro pasado. El spec afirma eso.
 */

const PLAN_ID = "plan-free-1";

async function setup(page: Page, opts: { onSubmit?: (req: object) => void } = {}): Promise<void> {
  await seedSession(page);
  // Migas de pan del plan (la cabecera de la página lo pide).
  await page.route(apiRoute(`/plans/${PLAN_ID}`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: PLAN_ID,
        project_id: "proj-1",
        title: "Plan de prueba",
        status: "in_progress",
      }),
    }),
  );
  await page.route(apiRoute(`/plans/${PLAN_ID}/escalated-tasks`), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tasks: [] }),
    }),
  );
  await page.route(apiRoute(`/plans/${PLAN_ID}/free-task`), async (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    opts.onSubmit?.(body);
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ id: "task-new" }),
    });
  });
}

/**
 * Abre el diálogo con la garantía de que el cliente ya está hidratado.
 *
 * `escalated-empty` lo pinta la respuesta (mockeada) de `/escalated-tasks`, o
 * sea que sólo aparece cuando React corre y la query ha resuelto. Sin esta
 * espera el click puede caer sobre el HTML servido y no abrir nada.
 */
async function openFreeTaskDialog(page: Page): Promise<void> {
  await expect(page.getByTestId("escalated-empty")).toBeVisible();
  await page.getByTestId("free-task-open").click();
}

test("el diálogo de tarea libre abre desde la página de escaladas", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });

  // Cerrado, los campos no están en pantalla: es lo que hacía imposible el
  // spec anterior, y merece quedar afirmado.
  await expect(page.getByTestId("free-task-title")).toHaveCount(0);

  await openFreeTaskDialog(page);
  await expect(page.getByTestId("free-task-title")).toBeVisible();
  await expect(page.getByTestId("free-task-description")).toBeVisible();
  await expect(page.getByTestId("free-task-submit")).toBeVisible();
});

test("submit posts {title, description} to /free-task", async ({ page }) => {
  const submissions: object[] = [];
  await setup(page, { onSubmit: (req) => submissions.push(req) });
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });

  await openFreeTaskDialog(page);
  await page.getByTestId("free-task-title").fill("Refactor auth middleware");
  // La descripción es un `<MarkdownTextarea>`: el testid nombra el contenedor y
  // el `<textarea>` real lleva el sufijo `-edit`.
  await page.getByTestId("free-task-description-edit").fill("Compliance flagged sessions");
  await page.getByTestId("free-task-submit").click();

  // El acuse de recibo ya no es un texto: el diálogo se cierra al crearse.
  await expect(page.getByTestId("free-task-title")).toHaveCount(0);
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({
    title: "Refactor auth middleware",
    description: "Compliance flagged sessions",
  });
});

test("sin título no se puede enviar (y con título sí)", async ({ page }) => {
  const submissions: object[] = [];
  await setup(page, { onSubmit: (req) => submissions.push(req) });
  await page.goto(`/admin/plans/${PLAN_ID}/escalated`, { waitUntil: "domcontentloaded" });

  await openFreeTaskDialog(page);
  await expect(page.getByTestId("free-task-submit")).toBeDisabled();

  // Espacios en blanco no cuentan como título: la guarda hace `trim()`.
  await page.getByTestId("free-task-title").fill("   ");
  await expect(page.getByTestId("free-task-submit")).toBeDisabled();

  await page.getByTestId("free-task-title").fill("Tarea con título");
  await expect(page.getByTestId("free-task-submit")).toBeEnabled();
  expect(submissions).toHaveLength(0);
});
