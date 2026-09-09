import { expect, test, type Page, type Route } from "@playwright/test";
import { apiRoute } from "./helpers/api";
import { seedSession } from "./helpers/session";

/**
 * E2E (subset mockeado, sin backend) de la sección «Skills del agente» en
 * /admin/agents/{id} (`task_mk_12`).
 *
 * La sección lee el catálogo (`GET /skills`) y la asignación actual
 * (`GET /agents/{id}/skills`), agrupa por categoría con etiqueta humana, marca
 * las asignadas y guarda el conjunto declarativo con `PUT /agents/{id}/skills`.
 *
 * Cubre: agrupación + pre-marcado, toggle + Guardar con el cuerpo esperado,
 * buscador, y sólo lectura para un agente `global_builtin`.
 */
const TENANT_ID = "11111111-0000-0000-0000-000000000001";
const AGENT_ID = "33333333-0000-0000-0000-000000000001";
const BUILTIN_AGENT_ID = "33333333-0000-0000-0000-0000000000bb";
const FASTAPI_ID = "55555555-0000-0000-0000-000000000001";
const PYTEST_ID = "55555555-0000-0000-0000-000000000002";
const JIRA_ID = "55555555-0000-0000-0000-000000000003";

const TENANT_ADMIN = {
  user_id: "99999999-0000-0000-0000-000000000099",
  email: "admin@tenant.test",
  full_name: "Tenant Admin",
  is_system_admin: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

function agentBody(scope: string) {
  return {
    id: scope === "global_builtin" ? BUILTIN_AGENT_ID : AGENT_ID,
    tenant_id: TENANT_ID,
    name: "Backend Dev",
    description: "Builds backend features.",
    avatar_url: null,
    agent_type: "ai",
    role: "backend_dev",
    system_prompt: "You are a backend dev.",
    model_config: {},
    memory_scope: "private",
    review_capability: false,
    max_concurrent_tasks: 1,
    is_template: false,
    scope,
    project_id: null,
    forked_from_agent_id: null,
    forked_from_version: null,
    anchored_version: null,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    deleted_at: null,
  };
}

const CATALOG = [
  {
    id: FASTAPI_ID,
    name: "fastapi-patterns",
    category: "backend",
    description: "Patrones FastAPI.",
    prompt_fragment: "Usa routers por dominio.",
    is_builtin: true,
  },
  {
    id: PYTEST_ID,
    name: "pytest-style",
    category: "qa",
    description: "Tests con pytest.",
    prompt_fragment: "Un assert por comportamiento.",
    is_builtin: true,
  },
  {
    id: JIRA_ID,
    name: "atlassian-jira-triage",
    category: "atlassian",
    description: "Triaje de tickets.",
    prompt_fragment: "Consulta Jira antes de asumir.",
    is_builtin: false,
  },
];

function assignedRow(id: string) {
  const s = CATALOG.find((c) => c.id === id)!;
  return {
    skill_id: s.id,
    name: s.name,
    category: s.category,
    description: s.description,
    prompt_fragment: s.prompt_fragment,
    is_builtin: s.is_builtin,
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function setup(
  page: Page,
  opts: { scope?: string; assigned?: string[]; onPut?: (body: unknown) => void } = {},
): Promise<string> {
  const scope = opts.scope ?? "project_local";
  const id = scope === "global_builtin" ? BUILTIN_AGENT_ID : AGENT_ID;
  // Con estado: tras el PUT, el GET devuelve el conjunto nuevo (como la API real).
  // Si devolviera siempre el inicial, TanStack lo compartiría estructuralmente y
  // la sección no vería «cambio» alguno tras guardar.
  let assigned = opts.assigned ?? [];

  await seedSession(page);
  await page.route(apiRoute("/me"), (route) => json(route, TENANT_ADMIN));
  await page.route(apiRoute(`/agents/${id}`), (route) => json(route, agentBody(scope)));
  await page.route(apiRoute("/agents"), (route) => json(route, []));
  await page.route(apiRoute("/projects"), (route) => json(route, []));
  await page.route(apiRoute("/tools**"), (route) => json(route, []));
  await page.route(apiRoute(`/agents/${id}/tools`), (route) => json(route, []));
  await page.route(apiRoute(`/agents/${id}/knowledge-bases`), (route) => json(route, []));
  await page.route(apiRoute("/skills**"), (route) => json(route, CATALOG));
  await page.route(apiRoute(`/agents/${id}/skills`), async (route) => {
    if (route.request().method() === "PUT") {
      const sent = route.request().postDataJSON() as { skills: { skill_id: string }[] };
      opts.onPut?.(sent);
      assigned = sent.skills.map((s) => s.skill_id);
      await json(
        route,
        assigned.map((id) => assignedRow(id)),
      );
      return;
    }
    await json(route, assigned.map(assignedRow));
  });
  return id;
}

test("agrupa por categoría y pre-marca las skills asignadas", async ({ page }) => {
  const id = await setup(page, { assigned: [FASTAPI_ID] });
  await page.goto(`/admin/agents/${id}`, { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("agent-skills-section")).toBeVisible();
  await expect(page.getByTestId("agent-skills-group-backend")).toContainText("Backend");
  await expect(page.getByTestId("agent-skills-group-qa")).toContainText("QA / Testing");
  await expect(page.getByTestId("agent-skills-group-atlassian")).toContainText("Atlassian");

  await expect(page.getByTestId(`agent-skill-checkbox-${FASTAPI_ID}`)).toBeChecked();
  await expect(page.getByTestId(`agent-skill-checkbox-${PYTEST_ID}`)).not.toBeChecked();
  // Sin cambios no hay nada que guardar.
  await expect(page.getByTestId("agent-skills-save")).toBeDisabled();
});

test("marcar una skill y Guardar envía el conjunto declarativo completo", async ({ page }) => {
  let sent: unknown = undefined;
  const id = await setup(page, {
    assigned: [FASTAPI_ID],
    onPut: (b) => {
      sent = b;
    },
  });
  await page.goto(`/admin/agents/${id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("agent-skills-list")).toBeVisible();

  await page.getByTestId(`agent-skill-checkbox-${JIRA_ID}`).click();
  await expect(page.getByTestId("agent-skills-save")).toBeEnabled();
  await page.getByTestId("agent-skills-save").click();

  await expect.poll(() => sent).toBeTruthy();
  const body = sent as { skills: { skill_id: string }[] };
  expect(body.skills.map((s) => s.skill_id).sort()).toEqual([FASTAPI_ID, JIRA_ID].sort());
  await expect(page.getByTestId("agent-skills-save")).toBeDisabled();
});

test("el buscador filtra el catálogo por nombre", async ({ page }) => {
  const id = await setup(page);
  await page.goto(`/admin/agents/${id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("agent-skills-list")).toBeVisible();

  await page.getByTestId("agent-skills-search").fill("pytest");
  await expect(page.getByTestId(`agent-skill-row-${PYTEST_ID}`)).toBeVisible();
  await expect(page.getByTestId(`agent-skill-row-${FASTAPI_ID}`)).toHaveCount(0);
});

test("un agente global_builtin ve las skills en sólo lectura", async ({ page }) => {
  const id = await setup(page, { scope: "global_builtin", assigned: [PYTEST_ID] });
  await page.goto(`/admin/agents/${id}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("agent-skills-list")).toBeVisible();

  await expect(page.getByTestId("agent-skills-save")).toHaveCount(0);
  await expect(page.getByTestId(`agent-skill-checkbox-${PYTEST_ID}`)).toBeChecked();
  await expect(page.getByTestId(`agent-skill-checkbox-${PYTEST_ID}`)).toBeDisabled();
});
