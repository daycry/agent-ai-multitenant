// @vitest-environment jsdom

/**
 * `task_ui_21` — el asistente «Añadir conocimiento» de tres pasos: elegir o crear
 * base → subir documentos o pegar texto → indexado en vivo con reintento. La
 * concesión al proyecto es implícita: el usuario no ve la palabra «grant».
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

import { AddKnowledgeWizard } from "@/components/knowledge/add-knowledge-wizard";

const PROJECT_ID = "p-1";
const KB = {
  id: "kb-1",
  name: "Manuales",
  description: null,
  is_builtin: false,
  embedding_model_stale: false,
  category: null,
};

function doc(status: string, extra: Record<string, unknown> = {}) {
  return {
    id: "doc-1",
    kb_id: "kb-1",
    title: "notas.md",
    source_filename: "notas.md",
    source_mime_type: "text/markdown",
    source_size_bytes: 10,
    status,
    error_message: null,
    page_count: 0,
    indexed_at: null,
    created_at: "2026-09-29T00:00:00Z",
    updated_at: "2026-09-29T00:00:00Z",
    ...extra,
  };
}

const fetchMock = vi.fn();

function wire(documentSequence: Record<string, unknown>[][]) {
  let call = 0;
  apiFetchMock.mockImplementation((path: string, init?: { method?: string; body?: unknown }) => {
    if (path === "/knowledge-bases" && !init?.method) return Promise.resolve([KB]);
    if (path === "/knowledge-bases" && init?.method === "POST")
      return Promise.resolve({ ...KB, id: "kb-new", name: (init.body as { name: string }).name });
    if (path.endsWith("/projects") && init?.method === "POST") return Promise.resolve({});
    if (path.endsWith("/reindex")) return Promise.resolve(doc("pending"));
    // El sondeo pide el lote con UNA petición `?ids=`, no la página del listado.
    if (/\/documents\?.*ids=doc-1/.test(path)) {
      const seq = documentSequence[Math.min(call, documentSequence.length - 1)];
      call += 1;
      return Promise.resolve([seq[0]]);
    }
    return Promise.resolve([]);
  });
}

function mount(onDone = vi.fn(), onOpenChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <LanguageProvider>
      <QueryClientProvider client={client}>
        <AddKnowledgeWizard
          open
          onOpenChange={onOpenChange}
          projectId={PROJECT_ID}
          grantedKbIds={new Set()}
          onDone={onDone}
        />
      </QueryClientProvider>
    </LanguageProvider>,
  );
  return onDone;
}

beforeEach(() => {
  window.localStorage.setItem("admin-panel.lang", "es");
  fetchMock.mockResolvedValue({
    ok: true,
    status: 201,
    json: async () => doc("pending"),
    text: async () => "",
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe("AddKnowledgeWizard", () => {
  it("recorre los tres pasos: elegir base, pegar texto, indexado en vivo; concede el proyecto sin preguntar", async () => {
    wire([[doc("processing")], [doc("indexed", { indexed_at: "2026-09-29T00:01:00Z" })]]);
    const onDone = mount();

    // Paso 1
    expect((await screen.findByTestId("kb-wizard")).getAttribute("data-step")).toBe("1");
    fireEvent.click(await screen.findByTestId("kb-wizard-pick-kb-1"));
    fireEvent.click(screen.getByTestId("kb-wizard-next"));

    // Paso 2: texto pegado (la ingesta sólo admite ficheros: se sube como .md)
    expect(screen.getByTestId("kb-wizard").getAttribute("data-step")).toBe("2");
    fireEvent.change(screen.getByTestId("kb-wizard-text"), { target: { value: "hola mundo" } });
    fireEvent.click(screen.getByTestId("kb-wizard-submit"));

    // Paso 3
    await waitFor(() =>
      expect(screen.getByTestId("kb-wizard").getAttribute("data-step")).toBe("3"),
    );
    // La concesión implícita: POST /knowledge-bases/kb-1/projects con el proyecto.
    expect(apiFetchMock).toHaveBeenCalledWith("/knowledge-bases/kb-1/projects", {
      method: "POST",
      body: { project_id: PROJECT_ID },
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, { body: FormData }];
    expect(url).toContain("/knowledge-bases/kb-1/documents");
    expect((init.body.get("file") as File).name).toMatch(/\.md$/);

    await waitFor(
      () =>
        expect(screen.getByTestId("kb-wizard-doc-doc-1").getAttribute("data-status")).toBe(
          "indexed",
        ),
      { timeout: 6000 },
    );
    fireEvent.click(screen.getByTestId("kb-wizard-done"));
    expect(onDone).toHaveBeenCalled();
  });

  it("crear una base nueva la crea ya concedida al proyecto", async () => {
    wire([[doc("indexed")]]);
    mount();
    fireEvent.click(await screen.findByTestId("kb-wizard-create-new"));
    fireEvent.change(screen.getByTestId("kb-wizard-new-name"), { target: { value: "Legal" } });
    fireEvent.click(screen.getByTestId("kb-wizard-next"));
    fireEvent.change(screen.getByTestId("kb-wizard-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("kb-wizard-submit"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/knowledge-bases", {
        method: "POST",
        body: expect.objectContaining({ name: "Legal", project_id: PROJECT_ID }),
      }),
    );
  });

  it("un documento fallido ofrece reintento y esconde el detalle técnico hasta desplegarlo", async () => {
    wire([
      [doc("failed", { error_message: "docling-serve 500: chunking failed" })],
      [doc("pending")],
    ]);
    mount();
    fireEvent.click(await screen.findByTestId("kb-wizard-pick-kb-1"));
    fireEvent.click(screen.getByTestId("kb-wizard-next"));
    fireEvent.change(screen.getByTestId("kb-wizard-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("kb-wizard-submit"));

    const retry = await screen.findByTestId("kb-wizard-retry-doc-1");
    // El vocabulario técnico sólo vive dentro del detalle plegado.
    const detail = screen.getByTestId("kb-wizard-error-detail-doc-1");
    expect(detail.tagName).toBe("DETAILS");
    expect(detail.textContent).toContain("docling-serve 500");
    expect(screen.getByTestId("kb-wizard-doc-doc-1").textContent).not.toMatch(/ingesta|ingest/i);

    fireEvent.click(retry);
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/knowledge-bases/kb-1/documents/doc-1/reindex", {
        method: "POST",
      }),
    );
  });

  it("indexed_empty es un estado terminal con su texto: el sondeo acaba", async () => {
    wire([[doc("indexed_empty")]]);
    mount();
    fireEvent.click(await screen.findByTestId("kb-wizard-pick-kb-1"));
    fireEvent.click(screen.getByTestId("kb-wizard-next"));
    fireEvent.change(screen.getByTestId("kb-wizard-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("kb-wizard-submit"));

    const row = await screen.findByTestId("kb-wizard-doc-doc-1");
    await waitFor(() => expect(row.getAttribute("data-status")).toBe("indexed_empty"));
    expect(row.textContent).toContain("Indexado sin contenido");
    const polls = () =>
      apiFetchMock.mock.calls.filter(([p]) => /\/documents\?.*ids=doc-1/.test(String(p))).length;
    const before = polls();
    await new Promise((r) => setTimeout(r, 1800));
    expect(polls()).toBe(before);
    // Nunca pide la página entera del listado (siempre con `ids`).
    expect(
      apiFetchMock.mock.calls.some(([p]) => String(p).endsWith("/knowledge-bases/kb-1/documents")),
    ).toBe(false);
  });

  it("cerrar el asistente tras un fallo parcial avisa con onDone (la base creada aparece)", async () => {
    wire([[doc("indexed")]]);
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({}),
      text: async () => "boom",
    });
    const onDone = vi.fn();
    const onOpenChange = vi.fn();
    mount(onDone, onOpenChange);
    fireEvent.click(await screen.findByTestId("kb-wizard-create-new"));
    fireEvent.change(screen.getByTestId("kb-wizard-new-name"), { target: { value: "Legal" } });
    fireEvent.click(screen.getByTestId("kb-wizard-next"));
    fireEvent.change(screen.getByTestId("kb-wizard-text"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("kb-wizard-submit"));
    await screen.findByTestId("kb-wizard-error");
    expect(screen.getByTestId("kb-wizard").getAttribute("data-step")).toBe("2");

    fireEvent.keyDown(screen.getByTestId("kb-wizard"), { key: "Escape" });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onDone).toHaveBeenCalled();
  });
});
