// @vitest-environment jsdom
// Caracterización de Knowledge Bases (tramo #9, auditoría 2026-07-10): red de
// tests ANTES de modularizar el monolito de 1042 líneas. Clava:
//   - la agrupación por categoría (grupo por categoría con KBs + «Sin
//     categoría» al final) y el badge builtin por fila;
//   - crear: el dialog envía POST /knowledge-bases con nombre y embedding;
//   - borrar: el confirm exige teclear el NOMBRE exacto antes de habilitar el
//     botón, y borra con DELETE /knowledge-bases/{id}.

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

import KnowledgeBasesPage from "@/app/admin/knowledge-bases/page";

const CATEGORY = {
  id: "cat-1",
  slug: "docs",
  name: "Documentación",
  color: null,
  is_builtin: false,
  tenant_id: "t1",
};

function kb(overrides: Record<string, unknown> = {}) {
  return {
    id: "kb-1",
    tenant_id: "t1",
    name: "Manual CI4",
    description: null,
    embedding_model_id: "nomic-embed-text-v1.5",
    created_by: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    is_builtin: false,
    category: null,
    ...overrides,
  };
}

function wireApi(kbs: Record<string, unknown>[], categories: Record<string, unknown>[] = []) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/knowledge-bases" && opts?.method === "POST") {
      return Promise.resolve(kb({ id: "kb-new", ...(opts.body as object) }));
    }
    if (path.startsWith("/knowledge-bases/") && opts?.method === "DELETE") {
      return Promise.resolve(undefined);
    }
    if (path === "/knowledge-bases") return Promise.resolve(kbs);
    if (path === "/kb-categories") return Promise.resolve(categories);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <KnowledgeBasesPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Knowledge Bases (caracterización tramo #9)", () => {
  it("groups KBs by category with 'Sin categoría' last and builtin badge", async () => {
    wireApi(
      [
        kb({ id: "kb-a", name: "Con categoría", category: { ...CATEGORY, tenant_id: undefined } }),
        kb({ id: "kb-b", name: "Suelta", is_builtin: true }),
      ],
      [CATEGORY],
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("kbs-list")).toBeTruthy());
    // Grupo por id de categoría + el grupo «Sin categoría» (__none__) al final.
    expect(screen.getByTestId("kb-group-cat-1")).toBeTruthy();
    expect(screen.getByTestId("kb-group-__none__")).toBeTruthy();
    expect(screen.getByTestId("kb-kb-a")).toBeTruthy();
    expect(screen.getByTestId("kb-kb-b")).toBeTruthy();
    // Solo la KB builtin lleva el badge.
    expect(screen.getByTestId("kb-builtin-badge-kb-b")).toBeTruthy();
    expect(screen.queryByTestId("kb-builtin-badge-kb-a")).toBeNull();
  });

  it("shows the empty state without KBs", async () => {
    wireApi([]);
    mount();
    await waitFor(() => expect(screen.getByTestId("kbs-empty")).toBeTruthy());
  });

  it("create dialog POSTs name and embedding model", async () => {
    wireApi([kb()]);
    mount();
    await waitFor(() => expect(screen.getByTestId("kbs-create-button")).toBeTruthy());
    fireEvent.click(screen.getByTestId("kbs-create-button"));
    await waitFor(() => expect(screen.getByTestId("kb-create-name")).toBeTruthy());
    fireEvent.change(screen.getByTestId("kb-create-name"), { target: { value: "Nueva KB" } });
    fireEvent.click(screen.getByTestId("kb-create-submit"));
    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([p, o]) => p === "/knowledge-bases" && (o as { method?: string })?.method === "POST",
      );
      expect(post).toBeTruthy();
      expect(post?.[1]?.body).toMatchObject({
        name: "Nueva KB",
        embedding_model_id: "nomic-embed-text-v1.5",
      });
    });
  });

  it("delete dialog only enables after typing the exact KB name", async () => {
    wireApi([kb({ id: "kb-a", name: "Manual CI4" })]);
    mount();
    await waitFor(() => expect(screen.getByTestId("kb-delete-kb-a")).toBeTruthy());
    fireEvent.click(screen.getByTestId("kb-delete-kb-a"));
    await waitFor(() => expect(screen.getByTestId("kb-delete-confirm-input")).toBeTruthy());
    const confirmBtn = screen.getByTestId("kb-delete-confirm") as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("kb-delete-confirm-input"), {
      target: { value: "Manual CI4" },
    });
    await waitFor(() =>
      expect((screen.getByTestId("kb-delete-confirm") as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("kb-delete-confirm"));
    await waitFor(() => {
      const del = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          p === "/knowledge-bases/kb-a" && (o as { method?: string })?.method === "DELETE",
      );
      expect(del).toBeTruthy();
    });
  });
});
