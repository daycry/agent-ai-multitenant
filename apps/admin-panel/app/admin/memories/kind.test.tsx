// @vitest-environment jsdom
/**
 * task_mem_04 — el tipo de observación (`kind`) de cada memoria en `/admin/memories`:
 * el vocabulario cerrado se pinta traducido y una memoria sin `kind` (las anteriores
 * a la migración 0152) sale «sin clasificar».
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import MemoriesPage from "@/app/admin/memories/page";
import { LanguageProvider } from "@/lib/lang-context";

const BASE = {
  tenant_id: "t1",
  type: "semantic",
  category: "semantic",
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
  recall_count: 0,
  created_by: { kind: "unknown", id: null, name: null },
  scope: "global",
};
const KINDS = ["summary", "decision", "bugfix", "pattern", "gotcha", "preference", "fact"] as const;
const TYPED = KINDS.map((kind) => ({ ...BASE, id: `m-${kind}`, kind, content: `memoria ${kind}` }));
const LEGACY = { ...BASE, id: "m-legacy", kind: null, content: "memoria antigua" };
const COUNTS = { all: 8, private: 0, team_shared: 0, project_shared: 0, global: 8 };

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/memories/counts") return Promise.resolve(COUNTS);
    if (path.startsWith("/memories")) return Promise.resolve([...TYPED, LEGACY]);
    return Promise.resolve([]);
  });
  window.localStorage.setItem("admin-panel.lang", lang);
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

describe("tipo de observación de la memoria", () => {
  it("cada kind del vocabulario se pinta traducido al castellano", async () => {
    renderIn("es");
    await waitFor(() => expect(screen.getByTestId("memory-kind-m-decision")).toBeTruthy());
    expect(screen.getByTestId("memory-kind-m-summary").textContent).toBe("Resumen");
    expect(screen.getByTestId("memory-kind-m-decision").textContent).toBe("Decisión");
    expect(screen.getByTestId("memory-kind-m-bugfix").textContent).toBe("Corrección");
    expect(screen.getByTestId("memory-kind-m-pattern").textContent).toBe("Patrón");
    expect(screen.getByTestId("memory-kind-m-gotcha").textContent).toBe("Trampa");
    expect(screen.getByTestId("memory-kind-m-preference").textContent).toBe("Preferencia");
    expect(screen.getByTestId("memory-kind-m-fact").textContent).toBe("Hecho");
  });

  it("y al inglés", async () => {
    renderIn("en");
    await waitFor(() => expect(screen.getByTestId("memory-kind-m-decision")).toBeTruthy());
    expect(screen.getByTestId("memory-kind-m-summary").textContent).toBe("Summary");
    expect(screen.getByTestId("memory-kind-m-bugfix").textContent).toBe("Bugfix");
    expect(screen.getByTestId("memory-kind-m-gotcha").textContent).toBe("Gotcha");
  });

  it("una memoria sin kind sale «sin clasificar», no en blanco", async () => {
    renderIn("es");
    await waitFor(() => expect(screen.getByTestId("memory-kind-m-legacy")).toBeTruthy());
    expect(screen.getByTestId("memory-kind-m-legacy").textContent).toBe("Sin clasificar");
    cleanup();
    renderIn("en");
    await waitFor(() => expect(screen.getByTestId("memory-kind-m-legacy")).toBeTruthy());
    expect(screen.getByTestId("memory-kind-m-legacy").textContent).toBe("Unclassified");
  });

  it("un kind que el panel aún no conoce no rompe la fila", async () => {
    apiFetchMock.mockImplementation((path: string) =>
      path === "/memories/counts"
        ? Promise.resolve(COUNTS)
        : Promise.resolve([{ ...BASE, id: "m-new", kind: "futuro", content: "x" }]),
    );
    window.localStorage.setItem("admin-panel.lang", "es");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <MemoriesPage />
        </LanguageProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("memory-kind-m-new")).toBeTruthy());
    expect(screen.getByTestId("memory-kind-m-new").textContent).toBe("Sin clasificar");
  });
});
