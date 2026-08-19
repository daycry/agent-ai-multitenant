// @vitest-environment jsdom
//
// Plan 06.10 `task_06_10_09` — el typeahead de KBs enseña a qué categoría
// pertenece cada resultado.
//
// Por qué importa, y no es cosmético: el combobox se usa para GRANTEAR una KB a
// un agente, y los nombres de KB colisionan entre categorías con toda
// naturalidad ("Manual" del stack y "Manual" de rol). Sin la categoría a la
// vista, elegir bien depende de recordar cuál era cuál; el secundario que había
// —el modelo de embedding— es el mismo para todas desde el ADR 0155, así que no
// desempata nada.
//
// El backend ya devolvía el embed `category` en CADA item de
// `GET /knowledge-bases` (`to_kb_response`, una sola query batch para todas las
// categorías: no hay N+1 que temer). O sea que esto es exactamente el patrón que
// documenta `verificar-antes-de-implementar.md` §5: el mecanismo estaba
// entregado entero y nadie lo leía.
//
// Se prueban las dos mitades: el mapeo puro (`kbOptionLabel`) y que lo que se
// PINTA en la fila del dropdown lo lleva de verdad — un helper correcto que
// nadie llama no arregla la pantalla.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { KbCombobox, kbOptionLabel } from "@/components/ui/kb-combobox";

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const STACK_CATEGORY = {
  id: "cat-1",
  slug: "stack",
  name: "Stack técnico",
  color: "#2563eb",
  is_builtin: true,
};

function kb(overrides: Record<string, unknown> = {}) {
  return {
    id: "kb-1",
    name: "Manual de CodeIgniter 4",
    embedding_model_id: "nomic-embed-text",
    category: STACK_CATEGORY,
    ...overrides,
  };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <KbCombobox value={null} onChange={() => {}} />
    </QueryClientProvider>,
  );
}

describe("kbOptionLabel", () => {
  it("prefija la categoría entre corchetes", () => {
    expect(kbOptionLabel(kb())).toBe("[stack] Manual de CodeIgniter 4");
  });

  it("sin categoría deja el nombre a secas", () => {
    // Ni `[] Manual` ni un `[sin categoría]` inventado: un corchete vacío es
    // ruido, y una etiqueta que el backend no manda sería una traducción
    // escondida en un componente.
    expect(kbOptionLabel(kb({ category: null }))).toBe("Manual de CodeIgniter 4");
    expect(kbOptionLabel(kb({ category: undefined }))).toBe("Manual de CodeIgniter 4");
  });

  it("tolera un embed de categoría sin slug", () => {
    // Defensa contra una respuesta parcial: sin slug no hay prefijo que poner,
    // y el usuario prefiere el nombre de la KB antes que `[undefined] …`.
    expect(kbOptionLabel(kb({ category: { ...STACK_CATEGORY, slug: "" } }))).toBe(
      "Manual de CodeIgniter 4",
    );
  });
});

describe("<KbCombobox />", () => {
  it("pinta `[slug] nombre` en la fila del dropdown, sin perder el modelo", async () => {
    apiFetchMock.mockResolvedValue([
      kb(),
      kb({ id: "kb-2", name: "Manual de estilo", category: { ...STACK_CATEGORY, slug: "role" } }),
    ]);

    mount();
    fireEvent.click(screen.getByTestId("kb-combobox-trigger"));

    await waitFor(() => {
      expect(screen.getByTestId("kb-combobox-option-kb-1")).toBeDefined();
    });
    expect(screen.getByTestId("kb-combobox-option-kb-1").textContent).toContain(
      "[stack] Manual de CodeIgniter 4",
    );
    expect(screen.getByTestId("kb-combobox-option-kb-2").textContent).toContain(
      "[role] Manual de estilo",
    );
    // El secundario que ya había no se pierde por el camino.
    expect(screen.getByTestId("kb-combobox-option-kb-1").textContent).toContain("nomic-embed-text");
  });

  it("una KB sin categoría se lista sin corchetes", async () => {
    apiFetchMock.mockResolvedValue([kb({ category: null })]);

    mount();
    fireEvent.click(screen.getByTestId("kb-combobox-trigger"));

    await waitFor(() => {
      expect(screen.getByTestId("kb-combobox-option-kb-1")).toBeDefined();
    });
    expect(screen.getByTestId("kb-combobox-option-kb-1").textContent).not.toContain("[");
  });
});
