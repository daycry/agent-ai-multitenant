// @vitest-environment jsdom
// ADR 0128 fase 4 — la pestaña "Avanzadas" de asignación de tools por-agente ya
// NO ofrece las tools MCP como asignables (las aporta el proyecto). Clava:
//   - las tools MCP (implementation_type mcp_tool / category "mcp") se excluyen
//     de la lista assignable y del contador de "Avanzadas";
//   - se muestra la nota informativa que redirige a la sección MCP del proyecto;
//   - las tools custom NO-MCP (http_endpoint/python_function/docker_command)
//     SIGUEN siendo asignables en "Avanzadas".

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import { LanguageProvider } from "@/lib/lang-context";
import { AgentToolsSection } from "@/app/admin/agents/[id]/agent-tools-section";

const CATALOG = [
  // Básica (builtin).
  {
    id: "b1",
    name: "read_file",
    description: "Lee un fichero",
    category: "file",
    implementation_type: "builtin",
    security_level: "safe",
    is_builtin: true,
    is_runtime_wired: true,
  },
  // Custom NO-MCP → sigue siendo asignable en "Avanzadas".
  {
    id: "c1",
    name: "my_webhook",
    description: "Llama a un webhook",
    category: "custom",
    implementation_type: "http_endpoint",
    security_level: "safe",
    is_builtin: false,
    is_runtime_wired: true,
  },
  // MCP tool → NO asignable por-agente (ADR 0128).
  {
    id: "m1",
    name: "github-mcp.create_issue",
    description: "Crea un issue",
    category: "mcp",
    implementation_type: "mcp_tool",
    security_level: "sandboxed",
    is_builtin: false,
    is_runtime_wired: true,
  },
];

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/tools?limit=500") return Promise.resolve(CATALOG);
    if (path === "/agents/agent-1/tools") return Promise.resolve([]);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <AgentToolsSection agentId="agent-1" isReadOnly={false} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("AgentToolsSection — MCP no asignable por-agente (ADR 0128)", () => {
  it("excludes MCP tools from the assignable Advanced list and counts", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("agent-tools-tab-advanced")).toBeTruthy());

    // Solo la custom NO-MCP cuenta como avanzada (la MCP no).
    expect(screen.getByTestId("agent-tools-tab-advanced").textContent).toContain("(1)");

    // Abre la pestaña Avanzadas.
    fireEvent.click(screen.getByTestId("agent-tools-tab-advanced"));

    // Nota informativa presente.
    await waitFor(() => expect(screen.getByTestId("agent-tools-mcp-project-note")).toBeTruthy());

    // La tool custom NO-MCP sí es asignable.
    expect(screen.getByTestId("agent-tool-row-c1")).toBeTruthy();
    // La tool MCP NO aparece como asignable en ninguna parte.
    expect(screen.queryByTestId("agent-tool-row-m1")).toBeNull();
    expect(screen.queryByTestId("agent-tool-checkbox-m1")).toBeNull();
  });

  it("keeps the builtin in Basic and never shows the MCP tool there", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("agent-tools-tab-basic")).toBeTruthy());
    // Básica (default) muestra el builtin.
    expect(screen.getByTestId("agent-tool-row-b1")).toBeTruthy();
    // La MCP tampoco es básica.
    expect(screen.queryByTestId("agent-tool-row-m1")).toBeNull();
    // Contador de básicas = 1.
    expect(screen.getByTestId("agent-tools-tab-basic").textContent).toContain("(1)");
  });
});
