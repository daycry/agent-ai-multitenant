// @vitest-environment jsdom

/**
 * `task_ui_30` — las cuatro pantallas de recursos del tenant (Agentes, Equipos,
 * Agentes humanos y Tools) comparten el patrón `components/resource/`: contador
 * en la cabecera, búsqueda, tarjetas con los campos en el mismo sitio y panel
 * lateral de resumen con enlace a la ficha completa.
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
  useParams: () => ({}),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin",
  useSearchParams: () => new URLSearchParams(),
}));

import AgentsCatalogPage from "@/app/admin/agents/page";
import HumanAgentsPage from "@/app/admin/human-agents/page";
import TeamsListPage from "@/app/admin/teams/page";
import ToolsCatalogPage from "@/app/admin/tools/page";

const AGENTS = [
  {
    id: "a1",
    tenant_id: "t",
    name: "Ada Backend",
    description: null,
    role: "backend_dev",
    agent_type: "ai",
    scope: "global_builtin",
    project_id: null,
    forked_from_agent_id: null,
    is_template: false,
    teams: [{ id: "tm1", name: "Core" }],
    model_config: null,
  },
  {
    id: "a2",
    tenant_id: "t",
    name: "Linus QA",
    description: null,
    role: "qa",
    agent_type: "ai",
    scope: "global_builtin",
    project_id: null,
    forked_from_agent_id: null,
    is_template: false,
    teams: [],
    model_config: null,
  },
];

const TEAMS = [
  { id: "tm1", tenant_id: "t", name: "Core", description: null, is_builtin: true, members: [] },
  {
    id: "tm2",
    tenant_id: "t",
    name: "Plataforma",
    description: null,
    is_builtin: true,
    members: [{ agent_id: "a1", role_in_team: null, is_team_leader: true, assignment_priority: 1 }],
  },
];

const HUMANS = [
  {
    id: "h1",
    tenant_id: "t",
    name: "Revisora Sec",
    description: null,
    avatar_url: null,
    agent_type: "human",
    role: "security",
    scope: "global_tenant_template",
    is_template: false,
    forked_from_agent_id: null,
    config: null,
  },
  {
    id: "h2",
    tenant_id: "t",
    name: "Tester Ana",
    description: null,
    avatar_url: null,
    agent_type: "human",
    role: "qa",
    scope: "global_tenant_template",
    is_template: false,
    forked_from_agent_id: null,
    config: null,
  },
];

const TOOLS = [
  {
    id: "tool-1",
    tenant_id: "t",
    name: "read_file",
    description: "Lee ficheros",
    category: "filesystem",
    implementation_type: "builtin",
    implementation_ref: null,
    security_level: "safe",
    is_builtin: true,
    is_runtime_wired: true,
  },
  {
    id: "tool-2",
    tenant_id: "t",
    name: "mi_webhook",
    description: "Llama a un endpoint",
    category: "custom",
    implementation_type: "http_endpoint",
    implementation_ref: "https://x",
    security_level: "safe",
    is_builtin: false,
    is_runtime_wired: true,
  },
];

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/agents") return Promise.resolve(AGENTS);
    if (path === "/teams") return Promise.resolve(TEAMS);
    if (path === "/human-agents") return Promise.resolve(HUMANS);
    if (path.startsWith("/tools")) return Promise.resolve(TOOLS);
    return Promise.resolve([]);
  });
}

function mount(node: React.ReactNode, lang: "es" | "en" = "es") {
  wireApi();
  window.localStorage.setItem("admin-panel.lang", lang);
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

describe("Agentes con el patrón de recurso", () => {
  it("contador, campos fijos, búsqueda y panel con enlace a la ficha", async () => {
    mount(<AgentsCatalogPage />);
    expect((await screen.findByTestId("resource-count")).textContent).toBe("2 agentes");
    const card = within(await screen.findByTestId("agent-a1"));
    expect(card.getByTestId("resource-field-scope")).toBeTruthy();
    expect(card.getByTestId("resource-field-usage").textContent).toBe("1 equipo");
    // El endpoint no devuelve última actividad: no se inventa.
    expect(card.queryByTestId("resource-field-lastActivity")).toBeNull();
    // Se conservan testids previos.
    expect(screen.getByTestId("agents-filters")).toBeTruthy();
    expect(screen.getByTestId("new-agent-button")).toBeTruthy();

    fireEvent.change(screen.getByTestId("agents-search"), { target: { value: "linus" } });
    expect(screen.queryByTestId("agent-a1")).toBeNull();
    expect(screen.getByTestId("agent-a2")).toBeTruthy();

    fireEvent.click(screen.getByTestId("resource-select-agent-a2"));
    expect(screen.getByTestId("resource-panel-detail-link").getAttribute("href")).toBe(
      "/admin/agents/a2",
    );
  });
});

describe("Equipos con el patrón de recurso", () => {
  it("contador, búsqueda, adopción intacta y panel con enlace", async () => {
    mount(<TeamsListPage />);
    expect((await screen.findByTestId("resource-count")).textContent).toBe("2 equipos");
    expect(await screen.findByTestId("team-adopt-tm1")).toBeTruthy();
    expect(
      within(screen.getByTestId("team-tm2")).getByTestId("resource-field-usage").textContent,
    ).toBe("1 miembro");
    fireEvent.change(screen.getByTestId("teams-search"), { target: { value: "plata" } });
    expect(screen.queryByTestId("team-tm1")).toBeNull();
    fireEvent.click(screen.getByTestId("resource-select-team-tm2"));
    expect(screen.getByTestId("resource-panel-detail-link").getAttribute("href")).toBe(
      "/admin/teams/tm2",
    );
  });
});

describe("Agentes humanos con el patrón de recurso", () => {
  it("contador, búsqueda, edición intacta y panel", async () => {
    mount(<HumanAgentsPage />);
    expect((await screen.findByTestId("resource-count")).textContent).toBe("2 agentes humanos");
    expect(await screen.findByTestId("ha-edit-h1")).toBeTruthy();
    expect(screen.getByTestId("ha-assigned-h1")).toBeTruthy();
    fireEvent.change(screen.getByTestId("human-agents-search"), { target: { value: "ana" } });
    expect(screen.queryByTestId("human-agent-h1")).toBeNull();
    fireEvent.click(screen.getByTestId("resource-select-human-agent-h2"));
    const panel = within(screen.getByTestId("resource-panel"));
    expect(panel.getByText("Tester Ana")).toBeTruthy();
    // No existe ficha completa de un agente humano: sin enlace.
    expect(panel.queryByTestId("resource-panel-detail-link")).toBeNull();
  });
});

describe("Tools con el patrón de recurso", () => {
  it("contador, filas y buscador intactos y panel de resumen", async () => {
    mount(<ToolsCatalogPage />);
    expect((await screen.findByTestId("resource-count")).textContent).toBe("2 tools");
    expect(await screen.findByTestId("tool-row-tool-1")).toBeTruthy();
    expect(screen.getByTestId("tools-facets")).toBeTruthy();
    fireEvent.change(screen.getByTestId("tools-search"), { target: { value: "webhook" } });
    expect(screen.queryByTestId("tool-row-tool-1")).toBeNull();
    fireEvent.click(screen.getByTestId("resource-select-tool-row-tool-2"));
    await waitFor(() => expect(screen.getByTestId("resource-panel")).toBeTruthy());
    expect(within(screen.getByTestId("resource-panel")).getByText("mi_webhook")).toBeTruthy();
  });
});
