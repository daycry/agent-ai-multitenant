// @vitest-environment jsdom

/**
 * `Ollama & Embeddings`, migrada al diccionario (prod-16 `task_prod16_04`).
 *
 * Las dos secciones entran: el descubrimiento de embeddings (solo lectura) y la
 * administración de modelos. Se afirma también lo que NO se traduce y es
 * deliberado: los nombres de modelo (`nomic-embed-text`) y el `detail` que
 * redacta el backend tras un pull.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

import OllamaPage from "@/app/admin/ollama/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/admin/embeddings/available-models") {
      return Promise.resolve({
        ollama_reachable: true,
        active_model: "nomic-embed-text",
        required_dim: 768,
        installed: [{ name: "nomic-embed-text", dim: 768, compatible: true, active: true }],
        recommended: ["nomic-embed-text"],
      });
    }
    if (path === "/admin/ollama/models") {
      return Promise.resolve({
        ollama_reachable: true,
        models: [{ name: "qwen2.5-coder:14b", size_bytes: 1024, modified_at: null }],
      });
    }
    return Promise.resolve([]);
  });
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <OllamaPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("ollama en castellano", () => {
  it("rinde las dos secciones con sus tablas", async () => {
    renderIn("es");

    expect(await screen.findByText("Modelo activo:")).toBeDefined();
    expect(screen.getByText("Dim requerida:")).toBeDefined();
    expect(screen.getByText("Ollama accesible")).toBeDefined();
    expect(screen.getByText("Embedder instalado")).toBeDefined();
    expect(await screen.findByText("Modelos Ollama")).toBeDefined();
    expect(screen.getByLabelText("Descargar (pull) un modelo")).toBeDefined();
    expect(screen.getByText("Tamaño")).toBeDefined();
  });
});

describe("ollama en inglés", () => {
  it("rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en");

    expect(await screen.findByText("Active model:")).toBeDefined();
    expect(screen.getByText("Required dim:")).toBeDefined();
    expect(screen.getByText("Ollama reachable")).toBeDefined();
    expect(screen.getByText("Installed embedder")).toBeDefined();
    expect(await screen.findByText("Ollama models")).toBeDefined();
    expect(screen.getByLabelText("Download (pull) a model")).toBeDefined();
    expect(screen.getByText("Size")).toBeDefined();
    expect(screen.getByLabelText("Delete qwen2.5-coder:14b")).toBeDefined();

    expect(screen.queryByText("Modelo activo:")).toBeNull();
    expect(screen.queryByText("Tamaño")).toBeNull();
    expect(screen.queryByLabelText("Borrar qwen2.5-coder:14b")).toBeNull();
  });

  it("los nombres de modelo NO se traducen: son identificadores de Ollama", async () => {
    renderIn("en");

    expect((await screen.findAllByText("nomic-embed-text")).length).toBeGreaterThan(0);
    expect(screen.getByText("qwen2.5-coder:14b")).toBeDefined();
  });
});
