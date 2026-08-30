// @vitest-environment jsdom

/**
 * El desplegable de «Equipo» del diálogo «Editar proyecto» — H9a del recorrido
 * E2E (`docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md`).
 *
 * ## Por qué un equipo built-in NO es una opción válida aquí
 *
 * Un equipo `is_builtin` vive en el tenant `Platform` y sus agentes también.
 * Las DOS vías que resuelven agentes a partir del equipo del proyecto filtran
 * por el tenant del proyecto, y ninguna hace excepción con la plataforma:
 *
 *   * chat — `api_server.chat.responder.team_role_agents` exige
 *     `Team.tenant_id == project.tenant_id` **y**
 *     `Agent.tenant_id == project.tenant_id`;
 *   * despacho — `orchestrator.dispatch.Dispatcher._candidates` exige
 *     `Agent.tenant_id == task.tenant_id`.
 *
 * Así que asignar un built-in no deja el proyecto «con un equipo compartido»:
 * lo deja con CERO agentes utilizables — peor que sin equipo, porque sin equipo
 * el pool de despacho aún incluye los globales del tenant. Ofrecerlo en el
 * desplegable era ofrecer una opción rota, y por eso aquí se listan pero NO se
 * pueden elegir: quien tenga uno asignado necesita seguir viendo cuál es.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

const PROJECT_ID = "11111111-2222-3333-4444-555555555555";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: PROJECT_ID }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import ProjectHubPage from "@/app/admin/projects/[id]/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const TEAMS = [
  { id: "team-builtin", name: "CodeIgniter 4", is_builtin: true },
  { id: "team-tenant", name: "CodeIgniter 4 (copia)", is_builtin: false },
];

function projectOn(teamId: string | null) {
  return {
    id: PROJECT_ID,
    name: "hello-world v2",
    description: null,
    status: "active",
    team_id: teamId,
    is_template: false,
    model_config: {},
    chat_model_config: {},
    git_config: null,
    worker_config: {},
    repository_config: {},
  };
}

function renderHub(lang: "es" | "en", teamId: string | null) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/me") {
      return Promise.resolve({
        user_id: "u-1",
        email: "admin@example.com",
        full_name: "Admin",
        is_system_admin: true,
        memberships: [],
        active_tenant_id: null,
      });
    }
    if (path === `/projects/${PROJECT_ID}`) return Promise.resolve(projectOn(teamId));
    if (path === `/projects/${PROJECT_ID}/capabilities`) {
      return Promise.resolve({
        entity_type: "project",
        entity_id: PROJECT_ID,
        saber: { knowledge_bases: [] },
        recordar: { memory_scope: null, memory: [] },
        ser: null,
        hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
        warnings: [],
      });
    }
    if (path === "/teams") return Promise.resolve(TEAMS);
    if (path === "/agents/provider-options") return Promise.resolve({ providers: [] });
    return Promise.resolve(null);
  });
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProjectHubPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

/** Abre «Editar» y devuelve el `<select>` de equipo con `/teams` ya resuelto. */
async function openTeamSelect(lang: "es" | "en", teamId: string | null = null) {
  renderHub(lang, teamId);
  fireEvent.click(await screen.findByTestId("project-edit-button"));
  const select = (await screen.findByTestId("edit-project-team")) as HTMLSelectElement;
  // `/teams` se pide con `enabled: open`: sin esperar a una opción real, el
  // desplegable aún sólo tiene «Sin equipo» y el test mediría la carrera.
  await screen.findByRole("option", { name: "CodeIgniter 4 (copia)" });
  return select;
}

function optionFor(select: HTMLSelectElement, value: string): HTMLOptionElement {
  const found = Array.from(select.querySelectorAll("option")).find((o) => o.value === value);
  expect(found).toBeTruthy();
  return found as HTMLOptionElement;
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("H9a — el desplegable distingue plataforma de tenant", () => {
  it("agrupa las dos familias bajo etiquetas distintas", async () => {
    const select = await openTeamSelect("es");

    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => g.label);
    expect(groups).toHaveLength(2);
    expect(groups.join(" | ")).toMatch(/plataforma/i);
    expect(groups.join(" | ")).toMatch(/tenant/i);

    expect(optionFor(select, "team-builtin").closest("optgroup")?.label).toMatch(/plataforma/i);
    expect(optionFor(select, "team-tenant").closest("optgroup")?.label).not.toMatch(/plataforma/i);
  });

  it("los de plataforma NO se pueden elegir; los del tenant sí", async () => {
    const select = await openTeamSelect("es");

    expect(optionFor(select, "team-builtin").disabled).toBe(true);
    expect(optionFor(select, "team-tenant").disabled).toBe(false);
  });

  it("un proyecto que YA tiene un built-in asignado lo ve, y ve por qué está roto", async () => {
    await openTeamSelect("es", "team-builtin");

    const warning = screen.getByTestId("edit-project-team-platform-warning");
    expect(warning.textContent).toMatch(/plataforma/i);
    // El aviso tiene que decir la SALIDA, no sólo el síntoma: adoptar el equipo.
    expect(warning.textContent).toMatch(/adopt/i);
  });

  it("sin built-in asignado no hay aviso que estorbe", async () => {
    await openTeamSelect("es", "team-tenant");

    expect(screen.queryByTestId("edit-project-team-platform-warning")).toBeNull();
  });

  it("los rótulos y el aviso se traducen y no dejan castellano debajo", async () => {
    const select = await openTeamSelect("en", "team-builtin");

    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => g.label);
    expect(groups.join(" | ")).toMatch(/platform/i);
    expect(groups.join(" | ")).not.toMatch(/plataforma/i);

    const warning = screen.getByTestId("edit-project-team-platform-warning");
    expect(warning.textContent).toMatch(/platform/i);
    expect(warning.textContent).not.toMatch(/plataforma/i);
  });
});
