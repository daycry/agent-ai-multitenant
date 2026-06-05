import { expect, test, type Page } from "@playwright/test";

/**
 * E2E del flujo completo de capacitación — Plan 06.17 task_06_17_16.
 *
 * ENTREGABLE: este spec se ESCRIBE pero NO se ejecuta en este plan (no hay
 * navegador en CI; requiere el stack vivo). Con el backend mockeado por
 * `page.route`, recorre el flujo de capacitación de extremo a extremo descrito en
 * el roadmap, apoyado en el modelo mental ÚNICO SABER/RECORDAR/SER/HACER
 * (`docs/04-reference/training-model.md`) y en el Hub de Capacidad
 * (`components/capability/capability-hub.tsx`, lógica pura `lib/capability/hub.ts`):
 *
 *   1. Abrir el Hub del agente → cuatro secciones con su ESTADO HONESTO.
 *   2. Configurar PERSONA (SER): el modelo del catálogo cerrado queda configurado
 *      → la sección SER deja de decir "Modelo no configurado".
 *   3. Asignar CONOCIMIENTO (SABER): una KB aparece con su NIVEL explícito; el
 *      agente la consulta (hits del rag-search, no vacío) — modelado por el conteo
 *      efectivo del contrato.
 *   4. Asignar una TOOL (HACER): la tool entra en el set EFECTIVO (compuesto con
 *      `effective-tools` de 06.18) y se ve ejecutable.
 *   5. MEMORIA activa (RECORDAR): con un scope no-private, la sección muestra
 *      memorias; el checklist Persona→Saber→Hacer→Recordar queda completo.
 *
 * Las cuatro vías se reflejan en un único contrato `GET /agents/{id}/capabilities`
 * (task_06_17_08). Cada "paso" del flujo se modela devolviendo un payload de
 * capabilities distinto, simulando el estado tras cada asignación. NUNCA se
 * inventan campos: los shapes espejan `CapabilitiesResponse` de `lib/capability/hub.ts`.
 */

const AGENT_ID = "aaaa1111-bbbb-2222-cccc-555555555555";

// --- agente base (built-in NO; tenant template editable) -------------------

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
    system_prompt: "PLANNING.",
    available: true,
  },
  {
    name: "execution",
    label_es: "Ejecución",
    label_en: "Execution",
    system_prompt: "EXECUTION.",
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

// --- payloads de capabilities por estado del flujo ------------------------
//
// Espejan `CapabilitiesResponse`. El frontend (lib/capability/hub.ts) deriva el
// estado HONESTO de cada sección a partir de estos campos; los avisos honestos
// viajan en `warnings`.

/** Estado inicial: persona configurada, pero sin KB, sin tools, sin memoria. */
const CAPS_EMPTY = {
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
  hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
  warnings: [],
};

/** Modelo NO configurado (model_config vacío) — SER en aviso. */
const CAPS_NO_MODEL = {
  ...CAPS_EMPTY,
  ser: {
    model_configured: false,
    provider: null,
    model: null,
    temperature: null,
    system_prompt_present: false,
  },
};

/** Tras asignar una KB de rol: SABER pasa a "1 KB asignada" con nivel Rol. */
const CAPS_WITH_KB = {
  ...CAPS_EMPTY,
  saber: {
    knowledge_bases: [
      { kb_id: "kb-1", name: "Backend conventions", level: "rol", is_builtin: false },
    ],
  },
};

/** Tras asignar la tool rag_search: HACER la incluye en el set efectivo. */
const CAPS_WITH_TOOL = {
  ...CAPS_WITH_KB,
  hacer: { effective: ["rag_search"], unrestricted: false, shell_exec_effective: false },
};

/** Estado final: + memoria de proyecto poblada → RECORDAR activa. */
const CAPS_FULL = {
  ...CAPS_WITH_TOOL,
  recordar: {
    memory_scope: "project_shared",
    memory: [{ scope: "project_shared", count: 3 }],
  },
};

/** Agente GLOBAL en una tarea de proyecto → warning honesto bilingüe (ADR 0054). */
const CAPS_GLOBAL_AGENT = {
  ...CAPS_EMPTY,
  warnings: [
    {
      code: "global_agent_no_project_context",
      es: "Agente global: no ve conocimiento ni memoria de proyecto en esta vista (en una tarea de proyecto usará el contexto de la tarea, ADR 0054).",
      en: "Global agent: does not see project knowledge or memory in this view (in a project task it uses the task context, ADR 0054).",
    },
  ],
};

async function setup(page: Page, opts: { caps?: object; agent?: object } = {}): Promise<void> {
  const caps = opts.caps ?? CAPS_EMPTY;
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
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(caps) }),
  );
  // Sub-secciones de la ficha consultan sus propios endpoints; [] para que la
  // página renderice sin error (el Hub solo lee /capabilities).
  for (const sub of ["knowledge-bases", "tools", "skills"]) {
    await page.route(`**/agents/${AGENT_ID}/${sub}`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );
  }
  await page.route(`**/agents/${AGENT_ID}`, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(agent),
    });
  });
}

// ---------------------------------------------------------------------------
// 1. El Hub abre con las cuatro secciones y su estado honesto
// ---------------------------------------------------------------------------

test("el Hub muestra las cuatro secciones SABER/RECORDAR/SER/HACER", async ({ page }) => {
  await setup(page);
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("capability-hub")).toBeVisible();
  for (const key of ["saber", "recordar", "ser", "hacer"]) {
    await expect(page.getByTestId(`capability-section-${key}`)).toBeVisible();
  }
});

test("estado HONESTO inicial: sin conocimiento, sin acciones efectivas", async ({ page }) => {
  await setup(page, { caps: CAPS_EMPTY });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const saber = page.getByTestId("capability-status-saber");
  await expect(saber).toContainText("Sin conocimiento asignado");
  await expect(saber).toHaveAttribute("data-active", "false");
  const hacer = page.getByTestId("capability-status-hacer");
  await expect(hacer).toContainText("Sin acciones efectivas");
  await expect(hacer).toHaveAttribute("data-active", "false");
});

// ---------------------------------------------------------------------------
// 2. PERSONA (SER): modelo del catálogo cerrado configurado
// ---------------------------------------------------------------------------

test("SER en aviso cuando el modelo no está configurado", async ({ page }) => {
  await setup(page, { caps: CAPS_NO_MODEL });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const ser = page.getByTestId("capability-status-ser");
  await expect(ser).toContainText("Modelo no configurado");
  await expect(ser).toHaveAttribute("data-active", "false");
});

test("SER activa muestra proveedor·modelo del catálogo cerrado", async ({ page }) => {
  await setup(page, { caps: CAPS_EMPTY });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const ser = page.getByTestId("capability-status-ser");
  await expect(ser).toContainText("claude_sdk");
  await expect(ser).toContainText("claude-opus-4");
  await expect(ser).toHaveAttribute("data-active", "true");
  // El verbo de SER es "Editar" (training-model.md).
  await expect(page.getByTestId("capability-verb-ser")).toContainText("Editar");
});

// ---------------------------------------------------------------------------
// 3. SABER: asignar KB → el agente la consulta (con su NIVEL explícito)
// ---------------------------------------------------------------------------

test("asignar una KB de rol: SABER muestra la KB con su nivel y el verbo Asignar", async ({
  page,
}) => {
  await setup(page, { caps: CAPS_WITH_KB });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const saber = page.getByTestId("capability-status-saber");
  await expect(saber).toContainText("1 KB asignada");
  await expect(saber).toHaveAttribute("data-active", "true");
  // La KB aparece con su nivel explícito (Rol).
  await expect(page.getByTestId("capability-saber-kbs")).toContainText("Backend conventions");
  await expect(page.getByTestId("capability-kb-level-kb-1")).toContainText("Rol");
  // Verbo único en SABER.
  await expect(page.getByTestId("capability-verb-saber")).toContainText("Asignar");
});

// ---------------------------------------------------------------------------
// 4. HACER: asignar tool → entra en el set EFECTIVO (06.18) y se ve ejecutable
// ---------------------------------------------------------------------------

test("asignar rag_search: HACER lo incluye en el set efectivo", async ({ page }) => {
  await setup(page, { caps: CAPS_WITH_TOOL });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const hacer = page.getByTestId("capability-status-hacer");
  await expect(hacer).toContainText("1 acción efectiva");
  await expect(hacer).toHaveAttribute("data-active", "true");
  // La tool efectiva (compuesta con effective-tools de 06.18) se muestra ejecutable.
  await expect(page.getByTestId("capability-hacer-tools")).toBeVisible();
  await expect(page.getByTestId("capability-hacer-tool-rag_search")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 5. RECORDAR: memoria activa + checklist completo
// ---------------------------------------------------------------------------

test("memoria de proyecto activa: RECORDAR muestra memorias y el checklist se completa", async ({
  page,
}) => {
  await setup(page, { caps: CAPS_FULL });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const recordar = page.getByTestId("capability-status-recordar");
  await expect(recordar).toContainText("3 memorias");
  await expect(recordar).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("capability-recordar-scopes")).toContainText("Proyecto");

  // El checklist de onboarding (Persona→Saber→Hacer→Recordar) queda completo.
  for (const step of ["ser", "saber", "hacer", "recordar"]) {
    await expect(page.getByTestId(`capability-checklist-step-${step}`)).toHaveAttribute(
      "data-done",
      "true",
    );
  }
});

test("private NO memoriza: RECORDAR avisa con honestidad", async ({ page }) => {
  const capsPrivate = {
    ...CAPS_EMPTY,
    recordar: { memory_scope: "private", memory: [] },
  };
  await setup(page, { caps: capsPrivate });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const recordar = page.getByTestId("capability-status-recordar");
  await expect(recordar).toContainText("Privada: no memoriza");
  await expect(recordar).toHaveAttribute("data-active", "false");
});

// ---------------------------------------------------------------------------
// Aviso de agente global (ADR 0054), de primera clase en el Hub
// ---------------------------------------------------------------------------

test("agente global: el Hub avisa honestamente del contexto de proyecto (ADR 0054)", async ({
  page,
}) => {
  await setup(page, { caps: CAPS_GLOBAL_AGENT });
  await page.goto(`/admin/agents/${AGENT_ID}`, { waitUntil: "domcontentloaded" });
  const notice = page.getByTestId("capability-hub-global-agent-warning");
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("Agente global");
  await expect(notice).toContainText("ADR 0054");
});
