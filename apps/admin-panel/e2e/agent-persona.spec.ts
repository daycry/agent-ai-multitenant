import { expect, test, type Page } from "@playwright/test";

/**
 * E2E de la sección Persona (SER) del agente — Plan 06.17 task_06_17_11.
 *
 * ENTREGABLE: este spec se escribe pero NO se ejecuta en este plan (requiere el
 * stack vivo). Verifica, con el backend mockeado por `page.route`, las cinco
 * propiedades que la tarea exige:
 *
 *   1. El selector de proveedor ofrece SOLO los 4 del catálogo cerrado (ADR
 *      0021): claude_sdk / copilot / azure_foundry / ollama. Ningún quinto.
 *   2. El alta/edición ENVÍAN `model_config` (proveedor/modelo/temperatura +
 *      system_prompts.{es,en}); ningún agente nuevo nace `{}`.
 *   3. Vista "prompt efectivo" que combina el prompt del rol con el del modo de
 *      chat seleccionado (consumido de `GET /chat-modes`).
 *   4. La edición es/en escribe sobre la MISMA fuente que lee la tarjeta de la
 *      lista (`model_config.system_prompts`): colisión lista vs detalle resuelta.
 *   5. El modo `custom` aparece "No disponible aún" (deshabilitado).
 */

const AGENT_ID = "aaaa1111-bbbb-2222-cccc-444444444444";

const AGENT = {
  id: AGENT_ID,
  tenant_id: "t",
  name: "Backend Senior",
  description: "Plantilla del tenant",
  agent_type: "ai",
  role: "backend_dev",
  system_prompt: "Eres un backend senior.",
  model_config: {
    provider: "claude_sdk",
    model: "claude-opus-4",
    temperature: 0.2,
    system_prompts: { es: "Eres un backend senior.", en: "You are a backend senior." },
  },
  memory_scope: "project_shared",
  review_capability: false,
  max_concurrent_tasks: 2,
  is_template: true,
  scope: "global_tenant_template",
  project_id: null,
  forked_from_agent_id: null,
};

const CHAT_MODES = [
  {
    name: "planning",
    label_es: "Planning",
    label_en: "Planning",
    system_prompt: "Estás en el modo PLANNING.",
    available: true,
  },
  {
    name: "discussion",
    label_es: "Discusión",
    label_en: "Discussion",
    system_prompt: "Estás en el modo DISCUSSION.",
    available: true,
  },
  {
    name: "execution",
    label_es: "Ejecución",
    label_en: "Execution",
    system_prompt: "Estás en el modo EXECUTION.",
    available: true,
  },
  {
    name: "custom",
    label_es: "Personalizado",
    label_en: "Custom",
    system_prompt: "",
    available: false,
  },
];

// A minimal capabilities payload so the CapabilityHub on the detail page does
// not error; the persona spec does not assert on it.
const CAPABILITIES = {
  entity_type: "agent",
  entity_id: AGENT_ID,
  saber: { knowledge_bases: [] },
  recordar: { memory_scope: "project_shared", memory: [] },
  ser: {
    model_configured: true,
    provider: "claude_sdk",
    model: "claude-opus-4",
    temperature: 0.2,
    system_prompt_present: true,
  },
  hacer: { effective: [], unrestricted: true, shell_exec_effective: false },
  warnings: [],
};

async function setupDetail(
  page: Page,
  opts: { onPut?: (body: Record<string, unknown>) => void; agent?: object } = {},
): Promise<void> {
  const agent = opts.agent ?? AGENT;
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/chat-modes", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CHAT_MODES),
    }),
  );
  await page.route(`**/agents/${AGENT_ID}/capabilities`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CAPABILITIES),
    }),
  );
  // KBs / tools / skills sub-sections fetch their own endpoints; return [] so
  // the page renders without errors.
  await page.route(`**/agents/${AGENT_ID}/knowledge-bases`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`**/agents/${AGENT_ID}/tools`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`**/agents/${AGENT_ID}/skills`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`**/agents/${AGENT_ID}`, (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(agent),
      });
    }
    if (method === "PUT") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPut?.(body);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...agent, ...body }),
      });
    }
    return route.fallback();
  });
}

// ---------------------------------------------------------------------------
// Vista de la sección Persona (read-only)
// ---------------------------------------------------------------------------

test("la sección Persona muestra proveedor/modelo/temperatura del agente", async ({ page }) => {
  await setupDetail(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("persona-section")).toBeVisible();
  await expect(page.getByTestId("persona-summary-provider")).toContainText("Claude");
  await expect(page.getByTestId("persona-summary-model")).toContainText("claude-opus-4");
  await expect(page.getByTestId("persona-summary-temperature")).toContainText("0.2");
});

test("el prompt efectivo combina el rol con el modo de chat seleccionado", async ({ page }) => {
  await setupDetail(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  // Solo el rol al inicio.
  await expect(page.getByTestId("persona-effective-prompt-text")).toContainText(
    "Eres un backend senior.",
  );
  await expect(page.getByTestId("persona-effective-prompt-text")).not.toContainText("PLANNING");
  // Al elegir Planning, el prompt del modo se suma.
  await page.getByTestId("persona-mode-select").selectOption("planning");
  await expect(page.getByTestId("persona-effective-prompt-text")).toContainText("PLANNING");
  await expect(page.getByTestId("persona-effective-prompt-text")).toContainText(
    "Eres un backend senior.",
  );
});

test("el modo custom aparece 'No disponible aún' y deshabilitado", async ({ page }) => {
  await setupDetail(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("persona-custom-unavailable")).toBeVisible();
  const customOption = page.locator('[data-testid="persona-mode-select"] option[value="custom"]');
  await expect(customOption).toBeDisabled();
  await expect(customOption).toContainText("No disponible aún");
});

// ---------------------------------------------------------------------------
// Edición: envía model_config con la fuente única bilingüe
// ---------------------------------------------------------------------------

test("el selector de proveedor ofrece SOLO los 4 del catálogo cerrado (ADR 0021)", async ({
  page,
}) => {
  await setupDetail(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await page.getByTestId("agent-edit-button").click();
  const options = page.locator('[data-testid="edit-agent-provider"] option');
  await expect(options).toHaveCount(4);
  const values = await options.evaluateAll((els) =>
    els.map((el) => (el as HTMLOptionElement).value),
  );
  expect(values).toEqual(["claude_sdk", "copilot", "azure_foundry", "ollama"]);
});

test("guardar la persona ENVÍA model_config con provider/model/temperature + system_prompts", async ({
  page,
}) => {
  const puts: Record<string, unknown>[] = [];
  await setupDetail(page, { onPut: (b) => puts.push(b) });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });

  await page.getByTestId("agent-edit-button").click();
  await page.getByTestId("edit-agent-provider").selectOption("ollama");
  await page.getByTestId("edit-agent-model").fill("llama3");
  await page.getByTestId("edit-agent-temperature").fill("0.5");
  // Edita el prompt EN sobre la fuente única.
  await page.getByTestId("edit-agent-prompt-en").fill("You are a backend senior, updated.");
  await page.getByTestId("edit-agent-save").click();

  await page.waitForTimeout(200);
  expect(puts).toHaveLength(1);
  const mc = puts[0].model_config as Record<string, unknown>;
  expect(mc.provider).toBe("ollama");
  expect(mc.model).toBe("llama3");
  expect(mc.temperature).toBe(0.5);
  const prompts = mc.system_prompts as Record<string, string>;
  expect(prompts.en).toBe("You are a backend senior, updated.");
  expect(prompts.es).toBe("Eres un backend senior.");
});

test("la edición es/en escribe sobre la MISMA fuente que muestra la vista (detalle)", async ({
  page,
}) => {
  await setupDetail(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  // La vista de detalle lee model_config.system_prompts (no el campo plano).
  await expect(page.getByTestId("agent-system-prompt-view")).toContainText(
    "Eres un backend senior.",
  );
  await page.getByTestId("agent-edit-button").click();
  // El editor siembra el textarea ES con la fuente bilingüe, no con otra cosa.
  await expect(page.getByTestId("edit-agent-prompt-es")).toHaveValue("Eres un backend senior.");
  await expect(page.getByTestId("edit-agent-prompt-en")).toHaveValue("You are a backend senior.");
});

// ---------------------------------------------------------------------------
// Alta: el diálogo nuevo agente envía model_config (nunca {})
// ---------------------------------------------------------------------------

async function setupCatalog(
  page: Page,
  opts: { onPost?: (body: Record<string, unknown>) => void } = {},
): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("agentic.token", "e2e-fake-token");
  });
  await page.route("**/chat-modes", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CHAT_MODES),
    }),
  );
  await page.route("**/agents", (route) => {
    const method = route.request().method();
    if (method === "GET") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    }
    if (method === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      opts.onPost?.(body);
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ id: "new-agent-1", tenant_id: "t", ...body }),
      });
    }
    return route.fallback();
  });
}

test("alta de agente envía model_config poblado (ningún agente nace {})", async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  await setupCatalog(page, { onPost: (b) => posts.push(b) });
  await page.goto("/admin/agents", { waitUntil: "domcontentloaded" });

  await page.getByTestId("new-agent-button").click();
  await page.getByTestId("new-agent-name").fill("Agente Persona");
  await page.getByTestId("new-agent-system-prompt").fill("Eres un revisor estricto.");
  await page.getByTestId("new-agent-provider").selectOption("copilot");
  await page.getByTestId("new-agent-model").fill("gpt-4o");
  await page.getByTestId("new-agent-submit").click();

  await page.waitForTimeout(200);
  expect(posts).toHaveLength(1);
  const mc = posts[0].model_config as Record<string, unknown>;
  expect(mc).toBeTruthy();
  expect(mc.provider).toBe("copilot");
  expect(mc.model).toBe("gpt-4o");
  const prompts = mc.system_prompts as Record<string, string>;
  expect(prompts.es).toBe("Eres un revisor estricto.");
});
