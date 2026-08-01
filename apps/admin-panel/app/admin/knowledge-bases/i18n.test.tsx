// @vitest-environment jsdom

/**
 * `knowledge-bases`, migrada al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Por qué este módulo tardó tanto en entrar: el guard `check-i18n.mjs` sólo le
 * marcaba **3 atributos**, y eso hacía parecer que era un lote pequeño. No lo
 * era. Esos 3 eran la punta de ~2.100 líneas de castellano cableado repartidas
 * en cinco ficheros (la página, las secciones, el panel de documentos, el
 * diálogo de asignaciones y la pantalla de categorías). Traducir sólo los
 * atributos habría dejado la pantalla mitad en inglés y mitad en castellano,
 * que es exactamente el fallo que este plan cierra.
 *
 * Aquí se afirma la pantalla ENTERA en los dos idiomas, incluidos los cuatro
 * diálogos y el panel de documentos plegado — que es donde vive la mitad del
 * texto y donde un `useT()` olvidado no se ve hasta que alguien despliega una
 * fila.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import KnowledgeBasesPage from "@/app/admin/knowledge-bases/page";
import KbCategoriesPage from "@/app/admin/knowledge-bases/categories/page";

const STORAGE_KEY = "admin-panel.lang";

const CATEGORY = {
  id: "cat-1",
  tenant_id: "t1",
  slug: "docs",
  name: "Documentacion",
  color: "#64748b",
  is_builtin: false,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

const BUILTIN_CATEGORY = {
  ...CATEGORY,
  id: "cat-0",
  slug: "stack",
  name: "Stack",
  is_builtin: true,
};

const KB = {
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
};

function wireApi(kbs: unknown[] = [KB], categories: unknown[] = [CATEGORY, BUILTIN_CATEGORY]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/knowledge-bases") return Promise.resolve(kbs);
    if (path === "/kb-categories") return Promise.resolve(categories);
    return Promise.resolve([]);
  });
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

const kbs = (lang: "es" | "en", ...args: Parameters<typeof wireApi>) => {
  wireApi(...args);
  return renderIn(lang, <KnowledgeBasesPage />);
};

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("knowledge-bases en castellano", () => {
  it("rinde cabecera, acciones y el grupo «Sin categoría»", async () => {
    kbs("es");

    expect(await screen.findByText(/Bases de conocimiento del tenant/)).toBeDefined();
    expect(screen.getByRole("link", { name: /Categorías/ })).toBeDefined();
    expect(screen.getByRole("button", { name: /Crear KB/ })).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("kbs-list")).toBeTruthy());
    expect(screen.getByText(/Sin categoría/)).toBeDefined();
  });

  it("rinde el estado vacío", async () => {
    kbs("es", []);
    const empty = await screen.findByTestId("kbs-empty");
    expect(empty.textContent).toContain("Aún no hay KBs");
  });
});

describe("knowledge-bases en inglés", () => {
  it("traduce cabecera, acciones y el grupo sin categoría", async () => {
    kbs("en");

    expect(await screen.findByText(/Knowledge bases for this tenant/)).toBeDefined();
    expect(screen.getByRole("link", { name: /Categories/ })).toBeDefined();
    expect(screen.getByRole("button", { name: /New KB/ })).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("kbs-list")).toBeTruthy());
    expect(screen.getByText(/Uncategorized/)).toBeDefined();
  });

  it("no deja castellano por debajo en la lista", async () => {
    kbs("en");

    await screen.findByText(/Knowledge bases for this tenant/);
    await waitFor(() => expect(screen.getByTestId("kbs-list")).toBeTruthy());

    expect(screen.queryByText(/Bases de conocimiento del tenant/)).toBeNull();
    expect(screen.queryByText(/Sin categoría/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Crear KB/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Asignaciones" })).toBeNull();
  });

  it("traduce el estado vacío", async () => {
    kbs("en", []);
    const empty = await screen.findByTestId("kbs-empty");
    expect(empty.textContent).toContain("No KBs in this tenant yet");
  });

  it("traduce la fila de KB, incluidos los títulos de sus botones", async () => {
    kbs("en");

    await waitFor(() => expect(screen.getByTestId("kb-kb-1")).toBeTruthy());
    const row = within(screen.getByTestId("kb-kb-1"));
    expect(row.getByRole("button", { name: "Assignments" })).toBeDefined();
    expect(screen.getByTestId("kb-assignments-kb-1").getAttribute("title")).toBe(
      "See which projects and agents have a grant",
    );
    expect(screen.getByTestId("kb-grant-kb-1").getAttribute("title")).toBe("Give a project access");
  });

  it("traduce el diálogo de alta, el selector de categoría y su atajo «+»", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kbs-create-button"));
    await waitFor(() => expect(screen.getByTestId("kb-create-name")).toBeTruthy());

    expect(screen.getByText("Create knowledge base")).toBeDefined();
    expect(screen.getByText(/A KB is a container of indexed documents/)).toBeDefined();
    expect(screen.getByText("Name")).toBeDefined();
    expect(screen.getByText("Category")).toBeDefined();
    expect(screen.getByText(/Categories help organize the list/)).toBeDefined();
    expect(screen.getByText("Description")).toBeDefined();
    // La opción "sin categoría" del <select> y el title del botón "+".
    expect(screen.getByText("— Uncategorized —")).toBeDefined();
    expect(screen.getByTestId("kb-create-category-create").getAttribute("title")).toBe(
      "Create a new category",
    );
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDefined();
    expect(screen.getByTestId("kb-create-submit").textContent).toBe("Create KB");
  });

  it("traduce el mini-diálogo inline de categoría nueva", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kbs-create-button"));
    await waitFor(() => expect(screen.getByTestId("kb-create-category-create")).toBeTruthy());
    fireEvent.click(screen.getByTestId("kb-create-category-create"));

    await waitFor(() => expect(screen.getByTestId("cat-inline-slug")).toBeTruthy());
    expect(screen.getByText("New category")).toBeDefined();
    expect(screen.getByTestId("cat-inline-slug").getAttribute("placeholder")).toBe(
      "e.g. compliance-pci",
    );
    expect(screen.getByTestId("cat-inline-submit").textContent).toBe("Create");
  });

  it("traduce el diálogo de edición, incluido el aviso del modelo de embedding", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kb-edit-kb-1"));
    await waitFor(() => expect(screen.getByTestId("kb-edit-name")).toBeTruthy());

    expect(screen.getByText("Edit knowledge base")).toBeDefined();
    expect(screen.getByText("Embedding model")).toBeDefined();
    expect(screen.getByText(/The model is fixed per KB/)).toBeDefined();
    expect(screen.getByTestId("kb-edit-submit").textContent).toBe("Save");
  });

  it("traduce el diálogo de borrado con confirmación por nombre", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kb-delete-kb-1"));
    await waitFor(() => expect(screen.getByTestId("kb-delete-confirm-input")).toBeTruthy());

    expect(screen.getByText("Delete knowledge base")).toBeDefined();
    expect(screen.getByText(/irreversible/)).toBeDefined();
    expect(screen.getByText(/To confirm, type the KB name/)).toBeDefined();
    expect(screen.getByTestId("kb-delete-confirm").textContent).toBe("Delete permanently");
    // Y sigue exigiendo el nombre exacto: traducir no puede aflojar la guarda.
    expect((screen.getByTestId("kb-delete-confirm") as HTMLButtonElement).disabled).toBe(true);
  });

  it("traduce el diálogo de grant a proyecto", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kb-grant-kb-1"));
    await waitFor(() => expect(screen.getByTestId("kb-grant-project")).toBeTruthy());

    expect(screen.getByText("Give a project access")).toBeDefined();
    expect(screen.getByText("Target project")).toBeDefined();
    expect(screen.getByTestId("kb-grant-submit").textContent).toBe("Grant access");
  });

  it("traduce el panel de documentos que cuelga de la fila", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kb-toggle-docs-kb-1"));
    await waitFor(() => expect(screen.getByTestId("kb-docs-panel-kb-1")).toBeTruthy());

    expect(screen.getByText(/Documents \(0\)/)).toBeDefined();
    expect(screen.getByRole("button", { name: /Upload document/ })).toBeDefined();
    const empty = await screen.findByTestId("kb-docs-empty-kb-1");
    expect(empty.textContent).toContain("This KB has no documents yet");
  });

  it("traduce el diálogo de subida de documento", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kb-toggle-docs-kb-1"));
    await waitFor(() => expect(screen.getByTestId("kb-docs-upload-open-kb-1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("kb-docs-upload-open-kb-1"));

    await waitFor(() => expect(screen.getByTestId("kb-docs-upload-dialog")).toBeTruthy());
    const dialog = within(screen.getByTestId("kb-docs-upload-dialog"));
    expect(dialog.getByText("Upload a document to the KB")).toBeDefined();
    expect(dialog.getByText("File")).toBeDefined();
    expect(dialog.getByText("Title (optional)")).toBeDefined();
    expect(screen.getByTestId("kb-docs-upload-title").getAttribute("placeholder")).toBe(
      "Defaults to the file name",
    );
    expect(screen.getByTestId("kb-docs-upload-submit").textContent).toBe("Upload");
  });

  it("traduce el diálogo de asignaciones", async () => {
    kbs("en");

    fireEvent.click(await screen.findByTestId("kb-assignments-kb-1"));
    await waitFor(() => expect(screen.getByTestId("kb-assignments-dialog")).toBeTruthy());

    const dialog = within(screen.getByTestId("kb-assignments-dialog"));
    expect(dialog.getByText(/Assignments — Manual CI4/)).toBeDefined();
    const empty = await screen.findByTestId("kb-assignments-empty");
    expect(empty.textContent).toContain("This KB is not granted to any project or agent yet");
    expect(dialog.getByText("Grant to a project")).toBeDefined();
    expect(dialog.getByText("Choose a project…")).toBeDefined();
    expect(dialog.getByText(/Advanced: grant to a specific agent/)).toBeDefined();
  });
});

describe("categorías de KB en los dos idiomas", () => {
  function categoriesPage(lang: "es" | "en") {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/kb-categories") return Promise.resolve([CATEGORY, BUILTIN_CATEGORY]);
      return Promise.resolve([]);
    });
    return renderIn(lang, <KbCategoriesPage />);
  }

  it("rinde en castellano", async () => {
    categoriesPage("es");

    expect(await screen.findByText("Categorías de KBs")).toBeDefined();
    expect(screen.getByRole("button", { name: /Nueva categoría/ })).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("kb-cat-custom-list")).toBeTruthy());
  });

  it("traduce cabecera, secciones y diálogo de alta", async () => {
    categoriesPage("en");

    expect(await screen.findByText("KB categories")).toBeDefined();
    expect(screen.getByText(/Organize your knowledge bases into groups/)).toBeDefined();
    expect(screen.getByRole("button", { name: /New category/ })).toBeDefined();

    fireEvent.click(screen.getByTestId("kb-cat-create-button"));
    await waitFor(() => expect(screen.getByTestId("kb-cat-slug")).toBeTruthy());
    expect(screen.getByTestId("kb-cat-name").getAttribute("placeholder")).toBe(
      "e.g. Compliance PCI-DSS",
    );
    expect(screen.getByTestId("kb-cat-submit").textContent).toBe("Create category");

    expect(screen.queryByText("Categorías de KBs")).toBeNull();
  });

  it("traduce el diálogo de borrado de categoría", async () => {
    categoriesPage("en");

    await waitFor(() => expect(screen.getByTestId("kb-cat-delete-docs")).toBeTruthy());
    fireEvent.click(screen.getByTestId("kb-cat-delete-docs"));

    await waitFor(() => expect(screen.getByTestId("kb-cat-delete-confirm")).toBeTruthy());
    expect(screen.getByText("Delete category")).toBeDefined();
    expect(screen.getByText(/will be left without a category/)).toBeDefined();
    expect(screen.getByTestId("kb-cat-delete-confirm").textContent).toBe("Delete");
  });
});
