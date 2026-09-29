// @vitest-environment jsdom

/**
 * «Última actividad» en la tarjeta del agente (`task_ui_30`): sale de
 * `last_activity_at` del listado; un agente sin runs no pinta la fila (nunca un
 * «nunca» inventado ni un cero).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
  useRouter: () => ({ push: vi.fn() }),
}));

import AgentsCatalogPage from "@/app/admin/agents/page";

const BASE = {
  tenant_id: "t1",
  description: null,
  agent_type: "ai",
  role: "backend_dev",
  model_config: null,
  is_template: false,
  scope: "global_builtin",
  project_id: null,
  forked_from_agent_id: null,
  teams: [],
};
const ACTIVE = { ...BASE, id: "ag-1", name: "Activo", last_activity_at: "2026-09-20T10:00:00Z" };
const IDLE = { ...BASE, id: "ag-2", name: "Inactivo", last_activity_at: null };

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("agentes — última actividad", () => {
  it("pinta la fecha del último run y omite la fila si no hay", async () => {
    apiFetchMock.mockImplementation((path: string) =>
      Promise.resolve(path === "/agents" ? [ACTIVE, IDLE] : []),
    );
    window.localStorage.setItem("admin-panel.lang", "es");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <AgentsCatalogPage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    const active = within(await screen.findByTestId("agent-ag-1"));
    await waitFor(() => expect(active.getByTestId("resource-field-lastActivity")).toBeTruthy());
    expect(active.getByTestId("resource-field-lastActivity").textContent).toMatch(/2026/);
    const idle = within(screen.getByTestId("agent-ag-2"));
    expect(idle.queryByTestId("resource-field-lastActivity")).toBeNull();
  });
});
