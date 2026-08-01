// @vitest-environment jsdom

/**
 * `agents` (catálogo + hub del agente), migrada al diccionario y partida en
 * secciones (plan prod-16, `task_prod16_03` + `task_prod16_08`).
 *
 * Las dos pantallas traducían a mano —cuatro ternarios de idioma inline entre
 * las dos— y el resto del texto estaba cableado en castellano: con el
 * toggle en EN se leía "Catálogo de agentes", "Plantillas del Tenant",
 * "Personalizar (crear copia)" y "Borrar definitivamente".
 *
 * El hub además tenía 824 líneas. Aquí se afirman las tres piezas en que se
 * partió (editar, borrar y fork) en los dos idiomas: si un diálogo dejó de
 * abrirse tras mover el código, salta aquí y no en producción.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "ag-1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

import AgentsCatalogPage from "@/app/admin/agents/page";
import AgentHubPage from "@/app/admin/agents/[id]/page";

const STORAGE_KEY = "admin-panel.lang";

const AGENT = {
  id: "ag-1",
  tenant_id: "t1",
  name: "Backend Dev",
  description: "Escribe backend",
  agent_type: "ai",
  role: "backend_dev",
  system_prompt: "Eres backend",
  model_config: null,
  memory_scope: "team_shared",
  review_capability: true,
  max_concurrent_tasks: 3,
  is_template: false,
  scope: "global_tenant_template",
  project_id: null,
  forked_from_agent_id: null,
  teams: [],
};

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/agents") return Promise.resolve([AGENT]);
    if (path === "/agents/ag-1") return Promise.resolve(AGENT);
    // El hub monta `<CapabilityHub>`, que pide su propio endpoint y espera un
    // OBJETO. Devolverle el `[]` genérico revienta con "cannot read
    // 'knowledge_bases' of undefined" y el fallo se lee como un bug del
    // troceado — que es exactamente lo que este test debe poder descartar.
    if (path === "/agents/ag-1/capabilities") {
      return Promise.resolve({
        entity_type: "agent",
        entity_id: "ag-1",
        saber: { knowledge_bases: [] },
        recordar: { memory_scope: "team_shared", memory: [] },
        ser: null,
        hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
        warnings: [],
      });
    }
    return Promise.resolve([]);
  });
}

function mount(node: React.ReactNode, lang: "es" | "en") {
  wireApi();
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("catálogo de agentes", () => {
  it("en castellano rinde cabecera, filtros y pestañas", async () => {
    mount(<AgentsCatalogPage />, "es");

    expect(await screen.findByText("Catálogo de agentes")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("agents-tabs")).toBeTruthy());
    expect(screen.getByText("Pertenencia")).toBeDefined();
    expect(screen.getByTestId("tab-template").textContent).toContain("Plantillas del Tenant");

    // El e2e `agents-catalog.spec.ts:60` afirma este texto exacto tras pinchar
    // la pestaña "Locales del Proyecto": la migración NO puede cambiar la cara
    // castellana o rompe un spec que aquí no se puede ejecutar.
    fireEvent.click(screen.getByTestId("tab-local"));
    expect(await screen.findByText(/No hay agentes locales de proyecto/)).toBeDefined();
  });

  it("en inglés traduce cabecera, filtros, pestañas y vacíos", async () => {
    mount(<AgentsCatalogPage />, "en");

    expect(await screen.findByText("Agent catalogue")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("agents-tabs")).toBeTruthy());
    expect(screen.getByText("Membership")).toBeDefined();
    expect(screen.getByText("Team")).toBeDefined();
    expect(screen.getByTestId("tab-template").textContent).toContain("Tenant templates");
    expect(screen.getByTestId("tab-local").textContent).toContain("Project-local");
    expect(screen.getByRole("button", { name: /New agent/ })).toBeDefined();

    expect(screen.queryByText("Catálogo de agentes")).toBeNull();
    expect(screen.queryByText("Pertenencia")).toBeNull();

    fireEvent.click(screen.getByTestId("tab-local"));
    expect(await screen.findByText(/No project-local agents/)).toBeDefined();
    expect(screen.queryByText(/No hay agentes locales de proyecto/)).toBeNull();
  });

  it("en inglés traduce el diálogo de alta, incluido el fieldset de persona", async () => {
    mount(<AgentsCatalogPage />, "en");

    fireEvent.click(await screen.findByTestId("new-agent-button"));
    await waitFor(() => expect(screen.getByTestId("new-agent-name")).toBeTruthy());

    // "Persona (modelo)" era uno de los tres ternarios de idioma inline de
    // esta pantalla: es lo que la migración tenía que llevarse por delante.
    expect(screen.getByText("Persona (model)")).toBeDefined();
    expect(screen.queryByText("Persona (modelo)")).toBeNull();
    expect(screen.getByText("Tenant template (reusable)")).toBeDefined();
    expect(screen.getByRole("button", { name: "Create" })).toBeDefined();
  });
});

describe("recorte del prompt en la tarjeta", () => {
  /**
   * `promptIn` pasó de un ternario a mano a `pickLang`, y eso NO es equivalente:
   * `pickLang` cae al otro idioma cuando el pedido viene vacío, no sólo cuando
   * falta. Un agente con el prompt EN a cadena vacía pintaba una tarjeta sin
   * prompt aunque tuviera el ES escrito. Es el único cambio de COMPORTAMIENTO
   * de esta migración: si alguien lo revierte, este test lo dice.
   */
  it("cae al castellano cuando el prompt EN existe pero está vacío", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/agents") {
        return Promise.resolve([
          {
            ...AGENT,
            model_config: { system_prompts: { es: "Prompt en castellano", en: "" } },
          },
        ]);
      }
      return Promise.resolve([]);
    });
    window.localStorage.setItem(STORAGE_KEY, "en");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <AgentsCatalogPage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByTestId("tab-template"));
    expect(await screen.findByText("Prompt en castellano")).toBeDefined();
  });
});

describe("hub del agente", () => {
  it("en castellano rinde cabecera y acciones", async () => {
    mount(<AgentHubPage />, "es");

    await waitFor(() => expect(screen.getByTestId("agent-fields")).toBeTruthy());
    expect(screen.getByTestId("agent-fork-button").textContent).toContain(
      "Personalizar (crear copia)",
    );
    expect(screen.getByText("puede revisar")).toBeDefined();
  });

  it("en inglés traduce cabecera, badges y campos", async () => {
    mount(<AgentHubPage />, "en");

    await waitFor(() => expect(screen.getByTestId("agent-fields")).toBeTruthy());
    expect(screen.getByTestId("agent-fork-button").textContent).toContain(
      "Customize (make a copy)",
    );
    expect(screen.getByRole("button", { name: /Edit/ })).toBeDefined();
    expect(screen.getByText("can review")).toBeDefined();
    expect(screen.queryByText("puede revisar")).toBeNull();
    expect(screen.queryByText(/Personalizar/)).toBeNull();
  });

  it("en inglés traduce los tres diálogos partidos (editar, borrar, fork)", async () => {
    mount(<AgentHubPage />, "en");

    await waitFor(() => expect(screen.getByTestId("agent-fields")).toBeTruthy());

    fireEvent.click(screen.getByTestId("agent-edit-button"));
    await waitFor(() => expect(screen.getByTestId("edit-agent-name")).toBeTruthy());
    // El cuarto ternario de idioma inline del módulo vivía en esta leyenda.
    expect(screen.getByText("Persona (model and prompt)")).toBeDefined();
    expect(screen.getByText("Can review tasks")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByTestId("agent-delete-button"));
    await waitFor(() => expect(screen.getByTestId("delete-agent-confirm-input")).toBeTruthy());
    const del = within(screen.getByTestId("delete-agent-confirm").closest("div")!.parentElement!);
    expect(del.getByRole("button", { name: "Delete permanently" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByTestId("agent-fork-button"));
    await waitFor(() => expect(screen.getByTestId("agent-fork-dialog")).toBeTruthy());
    const fork = within(screen.getByTestId("agent-fork-dialog"));
    expect(fork.getByText("Target project")).toBeDefined();
    expect(fork.getByRole("button", { name: "Create copy" })).toBeDefined();
  });
});
