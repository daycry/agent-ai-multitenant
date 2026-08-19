import { expect, test, type Page } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { PROVIDER_OPTIONS, mockProviderOptions } from "./helpers/provider-options";
import { seedSession } from "./helpers/session";

/**
 * E2E for the agent edit + delete dialogs (Plan 06.6 task_06_6_07).
 *
 * Reparado el 2026-08-19 (subset mockeado de CI) por dos derivas reales:
 *
 *   1. **El prompt del diálogo es bilingüe** (`edit-agent-prompt-es` / `-en`,
 *      `PersonaPromptFields`). El campo único `edit-agent-system-prompt` ya no
 *      existe: la fuente es `model_config.system_prompts`, y el campo plano
 *      `system_prompt` se DERIVA de ella al guardar.
 *   2. **ADR 0082: Guardar exige una FILA de proveedor** (`provider_id`) y un
 *      modelo. Con el fixture anterior (sin `model_config`) el borrador nunca
 *      era válido y el botón quedaba deshabilitado para siempre — el spec
 *      esperaba 15 s a un click imposible. El último test de este fichero fija
 *      esa guarda a propósito, para que la próxima vez se lea en la suite y no
 *      en un timeout.
 */

const AGENT_ID = "edit11111-aaaa-bbbb-cccc-dddddddddddd";
const AGENT_NAME = "Frontend Senior";

const AGENT_FIXTURE = {
  id: AGENT_ID,
  tenant_id: "t",
  name: AGENT_NAME,
  description: "Plantilla",
  agent_type: "ai",
  role: "frontend_dev",
  system_prompt: "Eres un FE senior.",
  memory_scope: "private",
  review_capability: false,
  max_concurrent_tasks: 1,
  is_template: true,
  scope: "global_tenant_template",
  project_id: null,
  forked_from_agent_id: null,
  // Persona completa (ADR 0082): fila concreta + modelo. Deliberadamente SIN
  // `system_prompts`, para que el diálogo siga ejerciendo el respaldo "ES cae
  // al campo plano `system_prompt`".
  model_config: {
    provider_id: PROVIDER_OPTIONS[0].id,
    provider: PROVIDER_OPTIONS[0].kind,
    model: PROVIDER_OPTIONS[0].models[0],
    temperature: 0.2,
  },
};

async function setup(
  page: Page,
  opts: {
    onPut?: (body: Record<string, unknown>) => void;
    onDelete?: () => void;
    agent?: object;
  } = {},
): Promise<void> {
  await seedSession(page);
  await mockProviderOptions(page);
  await page.route(apiRoute("/agents"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    }),
  );
  await page.route(apiRoute(`/agents/${AGENT_ID}`), async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(opts.agent ?? AGENT_FIXTURE),
      });
    }
    if (method === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPut?.(body);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...AGENT_FIXTURE, ...body }),
      });
    }
    if (method === "DELETE") {
      opts.onDelete?.();
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });
}

test("edit dialog pre-fills current values", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("agent-edit-button").click();
  await expect(page.getByTestId("edit-agent-name")).toHaveValue(AGENT_NAME);
  await expect(page.getByTestId("edit-agent-role")).toHaveValue("frontend_dev");
  // La fuente es bilingüe; sin `system_prompts` en el fixture, el ES se siembra
  // del campo plano y el EN queda vacío (respaldo de `initialPrompts`).
  await expect(page.getByTestId("edit-agent-prompt-es")).toHaveValue("Eres un FE senior.");
  await expect(page.getByTestId("edit-agent-prompt-en")).toHaveValue("");
});

test("save sends PUT with updated payload", async ({ page }) => {
  const calls: Record<string, unknown>[] = [];
  await setup(page, { onPut: (body) => calls.push(body) });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-edit-button").click();
  await page.getByTestId("edit-agent-name").fill("Frontend Senior v2");
  await page.getByTestId("edit-agent-review-cap").check();
  await page.getByTestId("edit-agent-max-tasks").fill("4");
  await page.getByTestId("edit-agent-save").click();

  await page.waitForTimeout(200);
  expect(calls).toHaveLength(1);
  expect(calls[0]).toMatchObject({
    name: "Frontend Senior v2",
    review_capability: true,
    max_concurrent_tasks: 4,
    // El campo plano NOT NULL se deriva de la fuente única (ES, o EN si no hay).
    system_prompt: "Eres un FE senior.",
    model_config: {
      provider_id: PROVIDER_OPTIONS[0].id,
      model: PROVIDER_OPTIONS[0].models[0],
      system_prompts: { es: "Eres un FE senior." },
    },
  });
});

test("cancelar el borrado no deja la confirmación escrita para la próxima vez", async ({
  page,
}) => {
  // Regresión gemela de la que `project-delete.spec.ts` cazó el 2026-08-19: el
  // botón Cancelar cerraba el diálogo saltándose el reset, así que al reabrir el
  // nombre seguía tecleado y "Borrar" estaba habilitado de entrada. La
  // confirmación por nombre sólo protege si hay que teclearla CADA vez.
  let deleted = false;
  await setup(page, { onDelete: () => (deleted = true) });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-delete-button").click();
  await page.getByTestId("delete-agent-confirm-input").fill(AGENT_NAME);
  await expect(page.getByTestId("delete-agent-confirm")).toBeEnabled();
  await page.getByRole("button", { name: /cancelar/i }).click();

  await page.getByTestId("agent-delete-button").click();
  await expect(page.getByTestId("delete-agent-confirm-input")).toHaveValue("");
  await expect(page.getByTestId("delete-agent-confirm")).toBeDisabled();
  expect(deleted).toBe(false);
});

test("un agente legacy sin provider_id no se puede guardar hasta elegir fila (ADR 0082)", async ({
  page,
}) => {
  // `model_config` de antes del ADR 0082: sólo el kind, sin la fila concreta.
  // No se puede inferir qué fila era, así que el operador tiene que re-elegirla.
  const legacy = {
    ...AGENT_FIXTURE,
    model_config: { provider: "claude_sdk", model: "claude-opus-4", temperature: 0.2 },
  };
  await setup(page, { agent: legacy });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-edit-button").click();
  await expect(page.getByTestId("edit-agent-provider")).toHaveValue("");
  await expect(page.getByTestId("edit-agent-save")).toBeDisabled();

  // Al elegir la fila (y su modelo) el guardado se desbloquea.
  await page.getByTestId("edit-agent-provider").selectOption(PROVIDER_OPTIONS[0].id);
  await page.getByTestId("edit-agent-model").selectOption(PROVIDER_OPTIONS[0].models[0]);
  await expect(page.getByTestId("edit-agent-save")).toBeEnabled();
});

test("delete with confirm-by-name fires DELETE", async ({ page }) => {
  let deleted = false;
  await setup(page, { onDelete: () => (deleted = true) });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-delete-button").click();
  await expect(page.getByTestId("delete-agent-confirm")).toBeDisabled();

  await page.getByTestId("delete-agent-confirm-input").fill(AGENT_NAME);
  await expect(page.getByTestId("delete-agent-confirm")).toBeEnabled();
  await page.getByTestId("delete-agent-confirm").click();

  await page.waitForURL("**/admin/agents", { timeout: 3000 });
  expect(deleted).toBe(true);
});
