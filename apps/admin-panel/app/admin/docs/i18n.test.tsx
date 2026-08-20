// @vitest-environment jsdom

/**
 * `docs`, migrado al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Doce ficheros y **ninguno aparecía en las allowlists**: cero ternarios y sólo
 * ocho atributos, sobre ~2.300 líneas. La deuda de este módulo vivía casi entera
 * en texto JSX suelto —los seis estados de cada panel (idle, hint, cargando,
 * error, vacío, resultados)—, que es exactamente la forma que las dos guardas no
 * ven. Es el mismo aviso que ya dejó `knowledge-bases` en la segunda pasada,
 * llevado al extremo.
 *
 * Aquí se rinden las cuatro superficies en los DOS idiomas: la barra lateral con
 * su árbol, el buscador con sus dos modos, el panel de facetas y marcadores, y
 * el panel de lectura con su modo «Comparar» —que sólo aparece tras pulsar una
 * pestaña, y donde vive un tercio del texto del módulo.
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

// El App Router no existe bajo vitest: `useSearchParams` se sirve de un stub
// que devuelve los parámetros que cada caso quiera.
const searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {} }),
  useSearchParams: () => searchParams,
}));

import DocsPage from "@/app/admin/docs/page";
import { DocsBookmarksView } from "@/app/admin/docs/docs-bookmarks-view";
import { DocDiffView } from "@/app/admin/docs/doc-diff-view";
import { DocToc } from "@/app/admin/docs/doc-toc";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

function wireApi(projects: unknown[] = [{ id: "p1", name: "Demo", status: "active" }]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/projects") return Promise.resolve(projects);
    if (path.includes("/docs/tree")) return Promise.resolve({ folders: [], files: [] });
    return Promise.resolve({ hits: [] });
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

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("visor de docs en castellano", () => {
  it("rinde cabecera, pestañas del raíl, buscador y facetas", async () => {
    wireApi();
    renderIn("es", <DocsPage />);

    // El texto sale DOS veces: en la miga de pan y en la cabecera.
    expect((await screen.findAllByText("Documentación")).length).toBe(2);
    expect(screen.getByTestId("docs-rail-tab-explore").textContent).toContain("Explorar");
    expect(screen.getByTestId("docs-rail-tab-bookmarks").textContent).toContain("Marcadores");
    expect(screen.getByTestId("docs-search-tab-fulltext").textContent).toBe("Texto");
    expect(screen.getByTestId("docs-search-input").getAttribute("placeholder")).toBe(
      "Selecciona un proyecto para buscar",
    );
    expect(screen.getByText("Filtros")).toBeDefined();
    expect(screen.getByText("Categoría")).toBeDefined();
    expect(screen.getByTestId("docs-filter-category-01-overview").textContent).toBe(
      "Visión general",
    );
    expect(screen.getByTestId("docs-content-empty").textContent).toContain(
      "Selecciona un documento en el árbol",
    );
  });
});

describe("visor de docs en inglés", () => {
  it("traduce cabecera, raíl, buscador y su estado idle", async () => {
    wireApi();
    renderIn("en", <DocsPage />);

    expect((await screen.findAllByText("Documentation")).length).toBe(2);
    expect(screen.getByTestId("docs-rail-tab-explore").textContent).toContain("Explore");
    expect(screen.getByTestId("docs-rail-tab-bookmarks").textContent).toContain("Bookmarks");
    expect(screen.getByTestId("docs-search-tab-fulltext").textContent).toBe("Text");
    expect(screen.getByTestId("docs-search-tab-semantic").textContent).toBe("Semantic");
    expect(screen.getByTestId("docs-search-input").getAttribute("placeholder")).toBe(
      "Select a project to search",
    );
    expect(screen.getByTestId("docs-search-input").getAttribute("aria-label")).toBe(
      "Search the documentation",
    );
    expect(screen.getByTestId("docs-search-idle").textContent).toContain(
      "Select a project in the tree",
    );

    expect(screen.queryByText("Documentación")).toBeNull();
    expect(screen.queryByText("Explorar")).toBeNull();
  });

  it("traduce el panel de facetas, incluidas las siete carpetas canónicas", async () => {
    wireApi();
    renderIn("en", <DocsPage />);

    await screen.findAllByText("Documentation");
    expect(screen.getByText("Filters")).toBeDefined();
    expect(screen.getByText("Category")).toBeDefined();
    expect(screen.getByText("Type")).toBeDefined();
    expect(screen.getByTestId("docs-filter-category-01-overview").textContent).toBe("Overview");
    expect(screen.getByTestId("docs-filter-category-05-architecture-decisions").textContent).toBe(
      "Decisions (ADR)",
    );
    expect(screen.getByTestId("docs-filter-type-readme").textContent).toBe("README / index");

    expect(screen.queryByText("Visión general")).toBeNull();
  });

  it("traduce la barra lateral: encabezado, árbol y sus estados", async () => {
    wireApi();
    renderIn("en", <DocsPage />);

    expect(await screen.findByText("Projects")).toBeDefined();
    expect(screen.getByTestId("docs-sidebar").getAttribute("aria-label")).toBe(
      "Documentation tree",
    );
    fireEvent.click(await screen.findByTestId("docs-project-toggle-p1"));
    await waitFor(() => expect(screen.getByTestId("docs-tree-empty")).toBeTruthy());
    expect(screen.getByTestId("docs-tree-empty").textContent).toBe("No documents in this project.");

    expect(screen.queryByText("Proyectos")).toBeNull();
  });

  it("traduce el vacío de proyectos de la barra lateral", async () => {
    wireApi([]);
    renderIn("en", <DocsPage />);

    const empty = await screen.findByTestId("docs-projects-empty");
    expect(empty.textContent).toBe("You have no accessible projects.");
  });

  it("traduce el panel de lectura vacío", async () => {
    wireApi();
    renderIn("en", <DocsPage />);

    const empty = await screen.findByTestId("docs-content-empty");
    expect(empty.textContent).toContain("Select a document in the tree on the left");
  });
});

describe("marcadores de docs en los dos idiomas", () => {
  const BOOKMARK = {
    projectId: "p1",
    projectName: "Demo",
    relpath: "docs/05-architecture-decisions/0021-llm.md",
    addedAt: Date.now(),
  };

  function bookmarksView(lang: "es" | "en", bookmarks: (typeof BOOKMARK)[]) {
    return renderIn(
      lang,
      <DocsBookmarksView
        bookmarks={bookmarks}
        selectedProjectId={null}
        selectedPath={null}
        onOpenDoc={() => {}}
        onRemove={() => {}}
      />,
    );
  }

  it("rinde el filtro de recencia y el vacío en castellano", () => {
    bookmarksView("es", []);

    expect(screen.getByTestId("docs-bookmarks-recency-all").textContent).toBe("Todos");
    expect(screen.getByTestId("docs-bookmarks-recency-7").textContent).toBe("7 días");
    expect(screen.getByTestId("docs-bookmarks-empty").textContent).toContain(
      "Aún no has marcado documentos",
    );
  });

  it("traduce el filtro de recencia, el vacío y la categoría de la fila", () => {
    bookmarksView("en", [BOOKMARK]);

    expect(screen.getByTestId("docs-bookmarks-recency-all").textContent).toBe("All");
    expect(screen.getByTestId("docs-bookmarks-recency-1").textContent).toBe("Today");
    expect(screen.getByTestId("docs-bookmarks-recency-30").textContent).toBe("30 days");

    const row = within(screen.getByTestId("docs-bookmarks-list"));
    expect(row.getByText("Decisions (ADR)")).toBeDefined();
    expect(row.getByRole("button", { name: "Remove from bookmarks" })).toBeDefined();

    expect(screen.queryByText("Decisiones (ADR)")).toBeNull();
    expect(screen.queryByText("30 días")).toBeNull();
  });
});

describe("comparar versiones y tabla de contenidos", () => {
  it("traduce el formulario de diff y su estado idle", () => {
    renderIn("en", <DocDiffView projectId="p1" path="docs/a.md" />);

    expect(screen.getByText("Base version")).toBeDefined();
    expect(screen.getByText("New version")).toBeDefined();
    expect(screen.getByTestId("docs-diff-base-input").getAttribute("aria-label")).toBe(
      "Git ref of the base version",
    );
    expect(screen.getByTestId("docs-diff-submit").textContent).toContain("Compare");
    expect(screen.getByTestId("docs-diff-idle").textContent).toContain(
      "Enter two git refs and press",
    );

    expect(screen.queryByText("Versión base")).toBeNull();
  });

  it("traduce el vacío del diff cuando no hay documento abierto", () => {
    renderIn("en", <DocDiffView projectId={null} path={null} />);
    expect(screen.getByTestId("docs-diff-empty").textContent).toContain(
      "Select a document to compare",
    );
  });

  it("traduce la tabla de contenidos, incluido su vacío y su aria-label", () => {
    renderIn("en", <DocToc entries={[]} />);
    expect(screen.getByTestId("docs-toc-empty").textContent).toBe("No sections.");

    cleanup();
    renderIn("en", <DocToc entries={[{ id: "a", text: "Intro", level: 2 }]} />);
    expect(screen.getByTestId("docs-toc").getAttribute("aria-label")).toBe("Table of contents");
    expect(screen.getByText("On this page")).toBeDefined();
  });

  it("rinde la tabla de contenidos en castellano", () => {
    renderIn("es", <DocToc entries={[{ id: "a", text: "Intro", level: 2 }]} />);
    expect(screen.getByText("En esta página")).toBeDefined();
  });
});
