// @vitest-environment jsdom
/**
 * task_ui_31 — pestañas por ámbito con contador, ficha de procedencia y
 * buscador semántico de `/admin/memories`.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  recall_count: null,
  created_by: { kind: "unknown", id: null, name: null },
};

const FROM_AGENT = {
  ...BASE,
  id: "m-agent",
  scope: "project_shared",
  content: "El proyecto usa asyncpg.",
  project_id: "p1",
  project_name: "Proyecto Uno",
  agent_id: "a1",
  agent_name: "Backend Bot",
  execution_id: "abcdef12-0000-0000-0000-000000000000",
  created_by: { kind: "agent", id: "a1", name: "Backend Bot" },
};
const LEGACY = { ...BASE, id: "m-legacy", scope: "global", content: "Regla antigua." };
const COUNTS = { all: 7, private: 1, team_shared: 2, project_shared: 3, global: 1 };

function wireApi(searchHits: unknown[] = []) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/memories/counts") return Promise.resolve(COUNTS);
    if (path.startsWith("/memories/search")) return Promise.resolve(searchHits);
    if (path.startsWith("/memories")) return Promise.resolve([FROM_AGENT, LEGACY]);
    return Promise.resolve([]);
  });
}

function renderIn(lang: "es" | "en", searchHits: unknown[] = []) {
  wireApi(searchHits);
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

describe("pestañas por ámbito con contador", () => {
  it("cada pestaña enseña el contador que da el backend", async () => {
    renderIn("es");
    await waitFor(() => expect(screen.getByTestId("memories-count-all").textContent).toBe("7"));
    expect(screen.getByTestId("memories-count-private").textContent).toBe("1");
    expect(screen.getByTestId("memories-count-team_shared").textContent).toBe("2");
    expect(screen.getByTestId("memories-count-project_shared").textContent).toBe("3");
    expect(screen.getByTestId("memories-count-global").textContent).toBe("1");
  });

  it("pinchar una pestaña pide el listado de ese ámbito", async () => {
    renderIn("es");
    fireEvent.click(await screen.findByTestId("memories-scope-private"));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith("/memories?scope=private"));
  });
});

describe("ficha de procedencia", () => {
  it("enseña quién, dónde, cuándo y ejecución con nombres, y las recuperaciones como desconocidas", async () => {
    renderIn("es");
    const card = within(await screen.findByTestId("memory-provenance-m-agent"));
    expect(card.getByTestId("memory-who-m-agent").textContent).toBe("Backend Bot · agente");
    expect(card.getByTestId("memory-where-m-agent").textContent).toContain(
      "Proyecto: Proyecto Uno",
    );
    expect(card.getByTestId("memory-where-m-agent").textContent).toContain("Agente: Backend Bot");
    expect(card.getByTestId("memory-when-m-agent").textContent).toContain("2026");
    expect(card.getByTestId("memory-execution-m-agent").textContent).toBe("abcdef12");
    expect(card.getByTestId("memory-recall-m-agent").textContent).toBe("desconocido");
  });

  it("una memoria antigua sin huella sale con quién «desconocido» y sin ejecución", async () => {
    renderIn("es");
    const card = within(await screen.findByTestId("memory-provenance-m-legacy"));
    expect(card.getByTestId("memory-who-m-legacy").textContent).toBe("desconocido");
    expect(card.getByTestId("memory-where-m-legacy").textContent).toBe("Toda la organización");
    expect(card.queryByTestId("memory-execution-m-legacy")).toBeNull();
  });

  it("traduce la ficha al inglés", async () => {
    renderIn("en");
    const card = within(await screen.findByTestId("memory-provenance-m-legacy"));
    expect(card.getByTestId("memory-who-m-legacy").textContent).toBe("unknown");
    expect(card.getByTestId("memory-where-m-legacy").textContent).toBe("Whole organization");
    expect(card.getByText("Who:")).toBeDefined();
  });

  it("con recall_count informado lo enseña como número", async () => {
    wireApi();
    apiFetchMock.mockImplementation((path: string) =>
      path === "/memories/counts"
        ? Promise.resolve(COUNTS)
        : Promise.resolve([{ ...LEGACY, recall_count: 4 }]),
    );
    window.localStorage.setItem("admin-panel.lang", "es");
    render(
      <QueryClientProvider client={new QueryClient()}>
        <LanguageProvider>
          <MemoriesPage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId("memory-recall-m-legacy").textContent).toBe("4 veces"),
    );
  });
});

describe("buscador semántico", () => {
  it("busca por significado y enseña los aciertos con su ficha", async () => {
    renderIn("es", [{ memory: FROM_AGENT, score: 0.03 }]);
    await screen.findByTestId("memory-m-legacy");
    fireEvent.change(screen.getByTestId("memories-search-input"), {
      target: { value: "asyncpg pool" },
    });
    fireEvent.click(screen.getByTestId("memories-search-submit"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/memories/search?q=asyncpg%20pool"),
    );
    await waitFor(() => expect(screen.queryByTestId("memory-m-legacy")).toBeNull());
    expect(screen.getByTestId("memory-provenance-m-agent")).toBeDefined();
  });

  it("sin aciertos lo dice, y vaciar el cuadro vuelve al listado", async () => {
    renderIn("es", []);
    await screen.findByTestId("memory-m-legacy");
    const input = screen.getByTestId("memories-search-input");
    fireEvent.change(input, { target: { value: "nada" } });
    fireEvent.click(screen.getByTestId("memories-search-submit"));
    expect((await screen.findByTestId("memories-empty")).textContent).toContain(
      "Ninguna memoria visible casa",
    );
    fireEvent.change(input, { target: { value: "" } });
    expect(await screen.findByTestId("memory-m-legacy")).toBeDefined();
  });
});
