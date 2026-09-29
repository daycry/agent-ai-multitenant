// @vitest-environment jsdom

/**
 * `task_ui_21` (revisión adversarial, intento 1) — la pestaña Conocimiento del
 * proyecto: las cifras vienen agregadas en el LISTADO (sin una petición por fila),
 * los documentos sólo se piden al desplegar, una base existente —built-in
 * incluida— se activa sin subir nada, y los términos de ingesta viven plegados.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const PROJECT_ID = "p-1";
const apiFetchMock = vi.fn();
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "p-1" }) }));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
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

import ProjectKnowledgeBasesPage from "@/app/admin/projects/[id]/knowledge-bases/page";

function kb(id: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    name: `Base ${id}`,
    description: null,
    is_builtin: false,
    embedding_model_stale: false,
    platform_embedding_model: "bge-m3",
    category: null,
    ...extra,
  };
}

const GRANTED = kb("kb-1", {
  document_stats: {
    total: 3000,
    by_status: { indexed: 2999, failed: 1 },
    last_updated_at: "2026-09-01T00:00:00Z",
  },
});
const BUILTIN = kb("kb-global", { name: "Catálogo global", is_builtin: true });
const OTHER = kb("kb-2", { name: "Otra base" });

const FAILED_DOC = {
  id: "doc-1",
  kb_id: "kb-1",
  title: "guia.pdf",
  source_filename: "guia.pdf",
  source_mime_type: "application/pdf",
  source_size_bytes: 2048,
  status: "failed",
  error_message: "docling-serve 500: boom",
  page_count: 4,
  indexed_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

beforeEach(() => {
  window.localStorage.setItem("admin-panel.lang", "es");
  apiFetchMock.mockImplementation((path: string, init?: { method?: string }) => {
    if (path === `/projects/${PROJECT_ID}/knowledge-bases`) return Promise.resolve([GRANTED]);
    if (path === "/knowledge-bases" && !init?.method)
      return Promise.resolve([GRANTED, BUILTIN, OTHER]);
    if (path === "/knowledge-bases/kb-1/documents") return Promise.resolve([FAILED_DOC]);
    if (path.endsWith("/projects") && init?.method === "POST") return Promise.resolve({});
    return Promise.resolve([]);
  });
});
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <LanguageProvider>
      <QueryClientProvider client={client}>
        <ProjectKnowledgeBasesPage />
      </QueryClientProvider>
    </LanguageProvider>,
  );
}
const paths = () => apiFetchMock.mock.calls.map(([p]) => String(p));

describe("pestaña Conocimiento del proyecto", () => {
  it("las cifras salen del listado y no pide documentos hasta desplegar", async () => {
    mount();
    expect((await screen.findByTestId("kb-stats-count-kb-1")).textContent).toBe("3000 documentos");
    // Un fallo antiguo no se esconde tras la primera página.
    expect(screen.getByTestId("kb-stats-state-kb-1").textContent).toBe("Con fallos");
    expect(paths().some((p) => p.endsWith("/documents"))).toBe(false);
    expect(paths().some((p) => p.endsWith("/knowledge-bases/kb-1/projects"))).toBe(false);

    fireEvent.click(screen.getByTestId("kb-toggle-kb-1"));
    await screen.findByTestId("kb-doc-status-doc-1");
    expect(paths()).toContain("/knowledge-bases/kb-1/documents");
  });

  it("activa una base existente, built-in incluida, sin subir ningún documento", async () => {
    mount();
    // La ya concedida no se ofrece; la built-in y la ajena sí.
    expect(await screen.findByTestId("kb-activate-kb-global")).toBeDefined();
    expect(screen.queryByTestId("kb-activate-kb-1")).toBeNull();
    fireEvent.click(screen.getByTestId("kb-activate-kb-global"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/knowledge-bases/kb-global/projects", {
        method: "POST",
        body: { project_id: PROJECT_ID },
      }),
    );
  });

  it("no ofrece candidatos mientras el listado del proyecto carga ni si falla", async () => {
    const base = apiFetchMock.getMockImplementation()!;
    for (const behaviour of ["pending", "error"] as const) {
      apiFetchMock.mockImplementation((path: string, init?: { method?: string }) => {
        if (path === `/projects/${PROJECT_ID}/knowledge-bases`)
          return behaviour === "pending"
            ? new Promise(() => {})
            : Promise.reject(new Error("boom"));
        return base(path, init);
      });
      mount();
      await waitFor(() => expect(paths().includes("/knowledge-bases")).toBe(true));
      await new Promise((r) => setTimeout(r, 100));
      expect(screen.queryByTestId("kb-activate-section")).toBeNull();
      expect(screen.queryByTestId("kb-activate-kb-1")).toBeNull();
      cleanup();
      apiFetchMock.mockClear();
    }
  });

  it("los términos de ingesta y el error crudo sólo viven en un detalle plegado", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("kb-toggle-kb-1"));
    const row = await screen.findByTestId("kb-doc-doc-1");
    const detail = row.querySelector("details");
    expect(detail).not.toBeNull();
    const outside = row.cloneNode(true) as HTMLElement;
    outside.querySelector("details")?.remove();
    expect(outside.textContent).not.toMatch(/docling|application\/pdf|Reindexar|Progreso/);
    expect(detail?.textContent).toContain("docling-serve 500");
    expect(detail?.textContent).toContain("application/pdf");
  });
});
