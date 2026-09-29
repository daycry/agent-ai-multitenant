// @vitest-environment jsdom
/**
 * `task_ui_31` (revisión) — la cabecera enseña el total de `/memories/counts` y el
 * listado pagina de 50 en 50 con «Cargar más» (`offset` de `GET /memories`).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    roleInActiveTenant: "tenant_admin",
  }),
}));

import MemoriesPage from "@/app/admin/memories/page";
import { LanguageProvider } from "@/lib/lang-context";

const mem = (n: number) => ({
  id: `m-${n}`,
  tenant_id: "t1",
  scope: "global",
  type: "semantic",
  category: "semantic",
  content: `Memoria ${n}`,
  tags: [],
  user_id: null,
  team_id: null,
  project_id: null,
  source_execution_id: null,
  execution_id: null,
  agent_id: null,
  has_embedding: false,
  created_at: "2026-08-01T10:00:00Z",
  updated_at: "2026-08-01T10:00:00Z",
  recall_count: null,
  created_by: { kind: "unknown", id: null, name: null },
});
const TOTAL = 120;

function wire() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/memories/counts")
      return Promise.resolve({
        all: TOTAL,
        private: 0,
        team_shared: 0,
        project_shared: 0,
        global: TOTAL,
      });
    if (path.startsWith("/memories")) {
      const offset = Number(new URL(path, "http://x").searchParams.get("offset") ?? 0);
      const n = Math.min(50, TOTAL - offset);
      return Promise.resolve(Array.from({ length: n }, (_, i) => mem(offset + i)));
    }
    return Promise.resolve([]);
  });
}

function mount() {
  window.localStorage.setItem("admin-panel.lang", "es");
  wire();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <MemoriesPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("Memorias: paginación", () => {
  it("avisa de «mostrando N de M» y «Cargar más» pide el siguiente offset hasta agotar", async () => {
    mount();
    expect((await screen.findByTestId("memories-showing")).textContent).toBe("Mostrando 50 de 120");
    expect(document.querySelectorAll('[data-testid^="memory-m-"]')).toHaveLength(50);

    fireEvent.click(screen.getByTestId("memories-load-more"));
    await waitFor(() =>
      expect(screen.getByTestId("memories-showing").textContent).toBe("Mostrando 100 de 120"),
    );
    expect(apiFetchMock).toHaveBeenCalledWith("/memories?offset=50");

    fireEvent.click(screen.getByTestId("memories-load-more"));
    await waitFor(() =>
      expect(screen.getByTestId("memories-showing").textContent).toBe("Mostrando 120 de 120"),
    );
    expect(apiFetchMock).toHaveBeenCalledWith("/memories?offset=100");
    expect(screen.queryByTestId("memories-load-more")).toBeNull();
  });
});
