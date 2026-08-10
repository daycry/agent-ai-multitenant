// @vitest-environment jsdom

/**
 * El diagnóstico de tools por agente, migrado al diccionario (prod-16,
 * `task_prod16_04`).
 *
 * Es una pantalla de VERIFICACIÓN: quien la abre está comprobando si un agente
 * ejecuta lo que cree. Que su banner de «solo lectura», sus avisos y sus badges
 * salgan en castellano con el toggle en EN no es un detalle cosmético — es
 * ruido justo donde alguien intenta leer con cuidado.
 *
 * Los tres ternarios de idioma que había aquí resolvían etiquetas de la
 * taxonomía de ADR 0049. Esas son datos bilingües, así que van por `label()` de
 * `lib/tools/taxonomy`, no por claves: duplicarlas en el diccionario reabriría
 * la divergencia que aquel ADR cerró.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/agent-tools-diagnostic",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import AgentToolsDiagnosticPage from "@/app/admin/projects/[id]/agent-tools-diagnostic/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const AGENT = {
  id: "ag-1",
  name: "Backend Dev",
  role: "backend_dev",
  scope: "project_local",
  tools: [],
};

const EFFECTIVE = {
  agent_id: "ag-1",
  mode: null,
  assigned: [
    {
      tool_id: "tool-1",
      name: "run_tests",
      canonical_names: [],
      category: "runtime",
      implementation_type: "docker_command",
      security_level: "privileged",
      is_builtin: true,
      executable_in_runtime: false,
    },
  ],
  effective: [],
  unrestricted: false,
  shell_exec_effective: false,
  warnings: [],
};

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/me") {
      return Promise.resolve({
        user_id: "u1",
        email: null,
        full_name: null,
        is_system_admin: true,
        memberships: [],
        active_tenant_id: null,
      });
    }
    if (path === "/projects/proj-1/agent-tools-diagnostic") {
      return Promise.resolve({
        project_id: "proj-1",
        agents: [AGENT],
        mcp_servers: [{ name: "atlassian", transport: "stdio", has_auth: true }],
      });
    }
    if (path === "/agents/ag-1/effective-tools") return Promise.resolve(EFFECTIVE);
    return Promise.resolve([]);
  });
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <AgentToolsDiagnosticPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("agent-tools-diagnostic en castellano", () => {
  it("rinde cabecera, banner de solo lectura y la tarjeta de MCP", async () => {
    renderIn("es");

    expect(await screen.findByText("Diagnóstico de tools por agente")).toBeDefined();
    expect(screen.getByTestId("agent-tools-diagnostic-readonly-banner").textContent).toContain(
      "Solo lectura",
    );
    expect(await screen.findByText("MCP servers del proyecto")).toBeDefined();
  });

  it("la fila de tool usa las etiquetas castellanas de la taxonomía", async () => {
    renderIn("es");

    const row = within(await screen.findByTestId("diagnostic-tool-tool-1"));
    expect(row.getByText("Contenedor")).toBeDefined();
    expect(row.getByText("Privilegiada")).toBeDefined();
    expect(row.getByText("No disponible aún")).toBeDefined();
  });
});

describe("agent-tools-diagnostic en inglés", () => {
  it("rinde cabecera, banner y tarjeta traducidos, sin castellano por debajo", async () => {
    renderIn("en");

    expect(await screen.findByText("Tool diagnostics by agent")).toBeDefined();
    expect(screen.getByTestId("agent-tools-diagnostic-readonly-banner").textContent).toContain(
      "Read-only",
    );
    expect(await screen.findByText("Project MCP servers")).toBeDefined();

    expect(screen.queryByText("Diagnóstico de tools por agente")).toBeNull();
    expect(screen.queryByText("MCP servers del proyecto")).toBeNull();
  });

  it("la fila de tool sigue el idioma activo", async () => {
    renderIn("en");

    const row = within(await screen.findByTestId("diagnostic-tool-tool-1"));
    expect(row.getByText("Container")).toBeDefined();
    expect(row.getByText("Privileged")).toBeDefined();
    expect(row.getByText("Not available yet")).toBeDefined();

    expect(screen.queryByText("Contenedor")).toBeNull();
    expect(screen.queryByText("No disponible aún")).toBeNull();
  });

  it("el breadcrumb y la descripción de la cabecera se traducen", async () => {
    renderIn("en");

    // El breadcrumb es lo que nombra la pestaña dentro del proyecto: si se
    // queda en castellano, el menú del proyecto sale mitad y mitad aunque la
    // pantalla esté traducida.
    expect(within(await screen.findByTestId("breadcrumb")).getByText("Tools by agent"));
    expect(screen.getByTestId("agent-tools-diagnostic-header").textContent).toContain(
      "actually runs",
    );

    expect(screen.queryByText("Tools por agente")).toBeNull();
  });
});
