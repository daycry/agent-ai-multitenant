import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { PROVIDER_OPTIONS, mockProviderOptions } from "./helpers/provider-options";
import { seedSession } from "./helpers/session";

/**
 * E2E for the "Nuevo agente" dialog (Plan 06.6 task_06_6_06).
 *
 * Reparado el 2026-08-19 (subset mockeado de CI). Tres derivas del panel que el
 * spec no había seguido, ninguna cosmética:
 *
 *   1. **El prompt es un `<MarkdownTextarea>`**, y ese componente pone el
 *      `data-testid` en el CONTENEDOR: el `<textarea>` real es
 *      `${testid}-edit`. Rellenar el contenedor da "Element is not an
 *      <input>...", no "no existe", que es lo que despistaba.
 *   2. **ADR 0082: se elige una FILA de proveedor (`provider_id`)**, no un kind.
 *      `validateDraft` exige `provider_id` + `model`, así que sin mockear
 *      `/agents/provider-options` y sin elegir fila y modelo el botón Crear
 *      NUNCA se habilita. El spec anterior no lo sabía y esperaba un click
 *      imposible.
 *   3. Por lo mismo, "submit deshabilitado sin project_id" era una aserción
 *      HUECA: el botón ya estaba deshabilitado por la persona incompleta, así
 *      que habría pasado igual con el bug que dice vigilar. Ahora completa la
 *      persona primero, para que lo único que falte sea el `project_id`.
 */

const PROJECT_ID = "proj-11111111-2222-3333-4444-555555555555";

async function setup(
  page: Page,
  opts: { onPost?: (body: Record<string, unknown>) => void } = {},
): Promise<void> {
  await seedSession(page);
  await mockProviderOptions(page);
  // El campo de proyecto es un `<ProjectCombobox>` que busca contra
  // `GET /projects?limit=20`, no un input de texto.
  await page.route(apiRoute("/projects?*"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: PROJECT_ID, name: "Proyecto A", status: "active" }]),
    }),
  );
  await page.route(apiRoute("/agents"), async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    }
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPost?.(body);
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "new-agent-1",
          tenant_id: "t",
          ...body,
          agent_type: "ai",
          memory_scope: "private",
          review_capability: false,
          max_concurrent_tasks: 1,
          is_template: body.scope === "global_tenant_template",
          forked_from_agent_id: null,
        }),
      });
    }
    return route.fallback();
  });
}

/**
 * Deja la persona VÁLIDA (fila de proveedor + modelo), que es lo que el ADR 0082
 * exige para habilitar Crear. Cualquier fila sirve; usamos la de Copilot porque
 * su lista de modelos no está vacía.
 */
async function fillPersona(page: Page): Promise<void> {
  const copilot = PROVIDER_OPTIONS.find((p) => p.kind === "copilot")!;
  await page.getByTestId("new-agent-provider").selectOption(copilot.id);
  await page.getByTestId("new-agent-model").selectOption(copilot.models[0]);
}

test("nuevo agente button is visible in the catalog header", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("new-agent-button")).toBeVisible();
});

test("dialog opens with default scope=global_tenant_template", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-agent-button").click();
  await expect(page.getByTestId("new-agent-scope-template")).toBeChecked();
  // project_id input is hidden by default.
  await expect(page.getByTestId("new-agent-project-id")).toHaveCount(0);
});

test("switching to project_local reveals project_id input", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-agent-button").click();
  await page.getByTestId("new-agent-scope-local").check();
  await expect(page.getByTestId("new-agent-project-id")).toBeVisible();
});

test("submit posts the payload with selected scope", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  await setup(page, { onPost: (body) => calls.push(body) });
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });

  await page.getByTestId("new-agent-button").click();
  await page.getByTestId("new-agent-name").fill("Agente Test");
  await page.getByTestId("new-agent-role").selectOption("reviewer");
  await page.getByTestId("new-agent-system-prompt-edit").fill("Eres un revisor estricto.");
  await fillPersona(page);
  await page.getByTestId("new-agent-submit").click();

  await page.waitForTimeout(200);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    name: "Agente Test",
    role: "reviewer",
    system_prompt: "Eres un revisor estricto.",
    scope: "global_tenant_template",
    project_id: null,
  });
  // ADR 0082 + 06.17: ningún agente nace con `model_config` vacío, y lo que se
  // persiste es la FILA elegida, no el kind.
  const copilot = PROVIDER_OPTIONS.find((p) => p.kind === "copilot")!;
  expect(calls[0].model_config).toMatchObject({
    provider_id: copilot.id,
    provider: "copilot",
    model: copilot.models[0],
    system_prompts: { es: "Eres un revisor estricto." },
  });
});

test("submit disabled when project_local + no project_id", async ({ page }) => {
  await setup(page);
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });
  await page.getByTestId("new-agent-button").click();
  await page.getByTestId("new-agent-name").fill("X");
  await page.getByTestId("new-agent-system-prompt-edit").fill("Y");
  await fillPersona(page);
  // Con nombre, prompt y persona completos el botón SÍ está habilitado: así la
  // aserción de abajo mide el project_id y no el resto del formulario.
  await expect(page.getByTestId("new-agent-submit")).toBeEnabled();

  await page.getByTestId("new-agent-scope-local").check();
  await expect(page.getByTestId("new-agent-submit")).toBeDisabled();

  // Y al elegir un proyecto vuelve a habilitarse: la guarda es el project_id.
  await page.getByTestId("new-agent-project-id-trigger").click();
  await page.getByTestId(`new-agent-project-id-option-${PROJECT_ID}`).click();
  await expect(page.getByTestId("new-agent-submit")).toBeEnabled();
});
