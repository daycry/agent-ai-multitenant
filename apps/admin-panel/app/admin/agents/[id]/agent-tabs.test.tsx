// @vitest-environment jsdom

/**
 * Ficha del agente por pestañas (`task_ui_32`, plan ui-reestructuracion): Identidad y
 * modelo · Skills y tools · Conocimiento · Memorias · Actividad. Cada bloque vive en
 * SU pestaña (las inactivas no se montan) y las de datos leen del endpoint con
 * `agent_id`, sin inventar nada que el dominio no guarde.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

import AgentHubPage from "@/app/admin/agents/[id]/page";

const AGENT = {
  id: "ag-1",
  tenant_id: "t1",
  name: "Backend Dev",
  description: "Escribe backend",
  agent_type: "ai",
  role: "backend_dev",
  system_prompt: "Eres backend",
  model_config: null,
  memory_scope: "private",
  review_capability: false,
  max_concurrent_tasks: 3,
  is_template: false,
  scope: "global_tenant_template",
  project_id: null,
  forked_from_agent_id: null,
  teams: [],
};

const MEMORY = {
  id: "m1",
  scope: "team_shared",
  type: "semantic",
  category: "semantic",
  content: "El login usa argon2",
  project_name: "Web",
  created_at: "2026-09-01T10:00:00Z",
};
const RUN = {
  id: "run-1",
  created_at: "2026-09-02T10:00:00Z",
  task_id: "t-1",
  task_title: "Migrar login",
  plan_title: "Plan login",
  agent_name: "Backend Dev",
  model: "claude",
  verdict: "done",
  duration_ms: 1000,
  total_tokens: 10,
  total_cost_usd: "0.01",
  display_currency: null,
  display_cost: null,
};

function mount() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/agents/ag-1") return Promise.resolve(AGENT);
    if (path === "/agents/ag-1/capabilities") {
      return Promise.resolve({
        entity_type: "agent",
        entity_id: "ag-1",
        saber: { knowledge_bases: [] },
        recordar: { memory_scope: "private", memory: [] },
        ser: null,
        hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
        warnings: [],
      });
    }
    if (String(path).startsWith("/memories")) return Promise.resolve([MEMORY]);
    if (String(path).startsWith("/runs")) return Promise.resolve([RUN]);
    return Promise.resolve([]);
  });
  window.localStorage.setItem("admin-panel.lang", "es");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <AgentHubPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("ficha del agente por pestañas", () => {
  it("tiene las cinco pestañas y abre en Identidad y modelo", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("agent-fields")).toBeTruthy());
    expect(screen.getByTestId("agent-tab-identity").textContent).toBe("Identidad y modelo");
    expect(screen.getByTestId("agent-tab-skills").textContent).toBe("Skills y tools");
    expect(screen.getByTestId("agent-tab-knowledge").textContent).toBe("Conocimiento");
    expect(screen.getByTestId("agent-tab-memories").textContent).toBe("Memorias");
    expect(screen.getByTestId("agent-tab-activity").textContent).toBe("Actividad");
    // Lo de las otras pestañas no está montado.
    expect(screen.queryByTestId("agent-tools-section")).toBeNull();
    expect(screen.queryByTestId("agent-skills-section")).toBeNull();
  });

  it("Skills y tools monta las dos secciones de asignación", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("agent-tab-skills"));
    expect(await screen.findByTestId("agent-tools-section")).toBeTruthy();
    expect(await screen.findByTestId("agent-skills-section")).toBeTruthy();
    expect(screen.queryByTestId("agent-fields")).toBeNull();
  });

  it("Memorias pide sólo las de este agente, avisa del ámbito privado y enlaza al hub", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("agent-tab-memories"));
    expect(await screen.findByText("El login usa argon2")).toBeTruthy();
    expect(screen.getByTestId("agent-private-memory-warning")).toBeTruthy();
    expect(
      apiFetchMock.mock.calls.some(([p]) => String(p).startsWith("/memories?agent_id=ag-1")),
    ).toBe(true);
    expect(screen.getByTestId("agent-memories-link").getAttribute("href")).toBe("/admin/memories");
  });

  it("Actividad lista los runs del agente y cada uno abre su detalle", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("agent-tab-activity"));
    const link = await screen.findByTestId("agent-run-link-run-1");
    expect(link.getAttribute("href")).toBe("/admin/executions/run-1");
    expect(
      apiFetchMock.mock.calls.some(
        ([p]) => String(p).includes("/runs?") && String(p).includes("agent_id=ag-1"),
      ),
    ).toBe(true);
  });
});
