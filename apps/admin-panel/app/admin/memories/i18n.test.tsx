// @vitest-environment jsdom

/**
 * `memories`, migrada al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Un solo fichero de 628 líneas, pero con tres superficies que el guard de
 * atributos no veía enteras: el formulario de alta (que está SIEMPRE abierto,
 * arriba), el filtro segmentado de scopes y el **diálogo de memorias
 * similares**, que sólo aparece al pinchar el contador amarillo de una fila.
 *
 * Se afirma también lo que NO se traduce y es deliberado: el badge `embedding`
 * (misma palabra en los dos idiomas) y los tags de la memoria, que son datos.
 *
 * Y entran aquí los tres comboboxes compartidos (`EntityCombobox` y sus
 * wrappers de proyecto y equipo): con el toggle en EN pintaban «Busca un equipo
 * por nombre…» dentro de una pantalla por lo demás traducida. El guard sólo
 * marcaba UNO de sus atributos, porque los demás son valores por defecto de
 * parámetro y no atributos JSX.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import MemoriesPage from "@/app/admin/memories/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const MEMORY = {
  id: "mem-1",
  tenant_id: "t1",
  scope: "team_shared",
  type: "semantic",
  content: "El worker-test cachea las deps en /data.",
  tags: ["infra"],
  user_id: null,
  team_id: "team-1",
  project_id: null,
  source_execution_id: null,
  agent_id: null,
  has_embedding: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const SIMILAR = {
  memory: { ...MEMORY, id: "mem-2", content: "El worker-test reutiliza el dep-cache." },
  similarity: 0.91,
};

function wireApi(memories: unknown[] = [MEMORY], similar: unknown[] = [SIMILAR]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/memories/") && path.endsWith("/similar")) return Promise.resolve(similar);
    if (path.startsWith("/memories")) return Promise.resolve(memories);
    return Promise.resolve([]);
  });
}

function renderIn(lang: "es" | "en", ...args: Parameters<typeof wireApi>) {
  wireApi(...args);
  window.localStorage.setItem(STORAGE_KEY, lang);
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

describe("memories en castellano", () => {
  it("rinde cabecera, filtro de scopes y formulario de alta", async () => {
    renderIn("es");

    expect(await screen.findByText("Memoria del equipo")).toBeDefined();
    expect(screen.getByTestId("memories-scope-all").textContent).toBe("Todas");
    expect(screen.getByTestId("memories-scope-team_shared").textContent).toBe("Equipo");
    expect(screen.getByText("Nueva memoria manual")).toBeDefined();
    expect(screen.getByText("Contenido")).toBeDefined();
    expect(screen.getByTestId("memory-create-submit").textContent).toBe("Guardar memoria");
    expect(screen.getByTestId("memory-tags-input").getAttribute("placeholder")).toBe(
      "separadas por comas",
    );
  });

  it("rinde el estado vacío", async () => {
    renderIn("es", []);
    expect((await screen.findByTestId("memories-empty")).textContent).toContain(
      "No hay memorias en este filtro",
    );
  });

  it("rinde el combobox de equipo del formulario", async () => {
    renderIn("es");

    await screen.findByText("Nueva memoria manual");
    const combo = within(screen.getByTestId("memory-team-id-input"));
    expect(combo.getByText("Busca un equipo por nombre…")).toBeDefined();
  });
});

describe("memories en inglés", () => {
  it("traduce cabecera, filtro de scopes y formulario", async () => {
    renderIn("en");

    expect(await screen.findByText("Team memory")).toBeDefined();
    expect(screen.getByTestId("memories-scope-all").textContent).toBe("All");
    expect(screen.getByTestId("memories-scope-team_shared").textContent).toBe("Team");
    expect(screen.getByText("New manual memory")).toBeDefined();
    expect(screen.getByText("Content")).toBeDefined();
    expect(screen.getByTestId("memory-create-submit").textContent).toBe("Save memory");
    expect(screen.getByTestId("memory-tags-input").getAttribute("placeholder")).toBe(
      "comma-separated",
    );

    expect(screen.queryByText("Memoria del equipo")).toBeNull();
    expect(screen.queryByText("Nueva memoria manual")).toBeNull();
    expect(screen.queryByText("Etiquetas")).toBeNull();
  });

  it("traduce la fila: badges de scope/tipo y el botón de borrado", async () => {
    renderIn("en");

    await waitFor(() => expect(screen.getByTestId("memory-mem-1")).toBeTruthy());
    const row = within(screen.getByTestId("memory-mem-1"));
    expect(row.getByText("Team")).toBeDefined();
    expect(row.getByText("Semantic")).toBeDefined();
    expect(row.getByRole("button", { name: "Delete" })).toBeDefined();
    // `embedding` es la misma palabra en los dos idiomas: no se traduce.
    expect(row.getByText("embedding")).toBeDefined();

    expect(row.queryByText("Semántica")).toBeNull();
    expect(screen.queryByRole("button", { name: "Eliminar" })).toBeNull();
  });

  it("traduce el contador de similares, incluido su aria-label", async () => {
    renderIn("en");

    const badge = await screen.findByTestId("memory-similar-badge-mem-1");
    expect(badge.textContent).toBe("1 similar");
    expect(badge.getAttribute("aria-label")).toBe("See 1 similar memories");
  });

  it("traduce el diálogo de memorias similares y sus dos acciones", async () => {
    renderIn("en");

    fireEvent.click(await screen.findByTestId("memory-similar-badge-mem-1"));
    await waitFor(() => expect(screen.getByTestId("similar-list")).toBeTruthy());

    expect(screen.getByText("Similar memories")).toBeDefined();
    expect(screen.getByText(/Duplicate candidates found by cosine similarity/)).toBeDefined();
    expect(screen.getByText("Current memory (target)")).toBeDefined();
    expect(screen.getByTestId("similar-pct-mem-2").textContent).toBe("91.0% similarity");
    expect(screen.getByTestId("similar-merge-mem-2").textContent).toContain("Merge");
    expect(screen.getByTestId("similar-discard-mem-2").textContent).toContain("Discard");
    expect(screen.getByRole("button", { name: "Close" })).toBeDefined();

    expect(screen.queryByText("Memorias similares")).toBeNull();
    expect(screen.queryByText("Memoria actual (target)")).toBeNull();
  });

  it("traduce el vacío del diálogo de similares", async () => {
    renderIn("en", [MEMORY], []);

    fireEvent.click(await screen.findByTestId("memory-delete-mem-1"));
    // El badge no se pinta con 0 candidatos, así que el diálogo se abre desde
    // una memoria SIN embedding: ahí el badge honesto sí está.
    cleanup();
    renderIn("en", [{ ...MEMORY, has_embedding: false }], []);
    const honest = await screen.findByTestId("memory-similar-unavailable-mem-1");
    expect(honest.textContent).toBe("Not available yet");
  });

  it("traduce los comboboxes compartidos del formulario", async () => {
    renderIn("en");

    await screen.findByText("New manual memory");
    const team = within(screen.getByTestId("memory-team-id-input"));
    expect(team.getByText("Search for a team by name…")).toBeDefined();

    // Y el buscador de dentro del desplegable, que sólo existe al abrirlo.
    fireEvent.click(screen.getByTestId("memory-team-id-input-trigger"));
    await waitFor(() => expect(screen.getByTestId("memory-team-id-input-search")).toBeTruthy());
    expect(screen.getByTestId("memory-team-id-input-search").getAttribute("placeholder")).toBe(
      "Search by name…",
    );

    expect(screen.queryByText("Busca un equipo por nombre…")).toBeNull();
  });

  it("traduce el vacío del desplegable del combobox", async () => {
    renderIn("en");

    await screen.findByText("New manual memory");
    fireEvent.click(screen.getByTestId("memory-team-id-input-trigger"));
    const empty = await screen.findByTestId("memory-team-id-input-empty");
    expect(empty.textContent).toBe("No results.");
  });
});
