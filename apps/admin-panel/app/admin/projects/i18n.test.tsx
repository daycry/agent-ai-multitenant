// @vitest-environment jsdom

/**
 * Las pantallas de `projects/` migradas al diccionario (plan prod-16,
 * `task_prod16_03`).
 *
 * Tres pantallas COMPLETAS, elegidas por ser autocontenidas: el listado de
 * proyectos (`projects/page.tsx`), la memoria del proyecto
 * (`projects/[id]/memories`) y la caché de dependencias
 * (`projects/[id]/dep-cache`). El hub del proyecto (`projects/[id]/page.tsx`) NO
 * entra a propósito: reparte su texto entre seis ficheros de
 * `components/projects/`, y traducir sólo el marco reproduciría la pantalla
 * mitad-y-mitad que este plan cierra.
 *
 * Cada caso rinde la pantalla en los DOS idiomas y afirma en ambos sentidos: en
 * inglés, que aparece el texto inglés Y que NO queda su cara castellana. Sin la
 * segunda mitad, un `useT()` olvidado en un sitio pasa desapercibido, porque el
 * resto de la pantalla sí traduce.
 *
 * Las tres comparten `<ProjectBreadcrumb>` / el enlace al listado, que decía
 * "Proyectos" fijo: con el toggle en EN la miga de pan seguía en castellano en
 * TODAS las sub-pantallas del proyecto. Se comprueba aquí porque es donde se ve.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: PROJECT_ID }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import DepCachePage from "@/app/admin/projects/[id]/dep-cache/page";
import ProjectMemoriesPage from "@/app/admin/projects/[id]/memories/page";
import ProjectsListPage from "@/app/admin/projects/page";

const STORAGE_KEY = "admin-panel.lang";
const PROJECT_ID = "11111111-2222-3333-4444-555555555555";

const PROJECT = {
  id: PROJECT_ID,
  name: "Demo",
  description: null,
  status: "active",
  team_id: null,
  is_template: false,
};

function routeApi(path: string): unknown {
  if (path === "/me") {
    return {
      user_id: "u-1",
      email: "admin@example.com",
      full_name: "Admin",
      is_system_admin: true,
      memberships: [],
      active_tenant_id: null,
    };
  }
  if (path === "/projects") return [PROJECT];
  if (path === `/projects/${PROJECT_ID}`) return PROJECT;
  if (path.startsWith("/memories?")) {
    return [
      {
        id: "m-1",
        tenant_id: "t-1",
        scope: "project_shared",
        type: "semantic",
        content: "El worker de tests usa pytest -q.",
        tags: ["tests"],
        project_id: PROJECT_ID,
        // Sin embedding: fuerza el badge de honestidad "No disponible aún".
        has_embedding: false,
        created_at: "2026-08-19T00:00:00Z",
        updated_at: "2026-08-19T00:00:00Z",
      },
    ];
  }
  if (path === "/runtime-templates") {
    return [
      {
        id: "python-pytest",
        label: { es: "Python (pytest)", en: "Python (pytest)" },
        dep_cache_mount: "/deps/python",
        network_policy: "restricted",
      },
      {
        id: "generic-shell",
        label: { es: "Shell genérico", en: "Generic shell" },
        dep_cache_mount: null,
        network_policy: "none",
      },
    ];
  }
  if (path === `/projects/${PROJECT_ID}/dep-cache/invalidate`) {
    return { runtime: "python-pytest", invalidated_count: 3, invalidated_paths: [] };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  apiFetchMock.mockImplementation((path: string) => Promise.resolve(routeApi(path)));
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

describe("projects/ — listado de proyectos", () => {
  it("en castellano rinde cabecera, acción de alta y la tarjeta sin descripción", async () => {
    renderIn("es", <ProjectsListPage />);

    expect(await screen.findByText("Proyectos")).toBeDefined();
    expect(
      screen.getByText("Proyectos activos del tenant. Las plantillas se eligen al crear."),
    ).toBeDefined();
    expect((await screen.findByTestId("new-project-button")).textContent).toContain(
      "Crear proyecto",
    );
    expect(await screen.findByText("Sin descripción.")).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <ProjectsListPage />);

    expect(await screen.findByText("Projects")).toBeDefined();
    expect(
      screen.getByText("The tenant's active projects. Templates are picked when creating one."),
    ).toBeDefined();
    expect((await screen.findByTestId("new-project-button")).textContent).toContain("New project");
    expect(await screen.findByText("No description.")).toBeDefined();

    expect(screen.queryByText("Proyectos")).toBeNull();
    expect(screen.queryByText("Sin descripción.")).toBeNull();
  });

  it("el estado vacío también se traduce (es donde entra un tenant nuevo)", async () => {
    apiFetchMock.mockImplementation((path: string) =>
      Promise.resolve(path === "/projects" ? [] : routeApi(path)),
    );
    window.localStorage.setItem(STORAGE_KEY, "en");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <ProjectsListPage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId("projects-empty")).toBeDefined();
    expect(
      screen.getByText("This tenant has no projects yet. Start from a template."),
    ).toBeDefined();
    expect(await screen.findByRole("link", { name: "Create the first one" })).toBeDefined();
  });
});

describe("projects/[id]/memories — memoria del proyecto", () => {
  it("en castellano rinde miga de pan, cabecera y el badge de honestidad", async () => {
    renderIn("es", <ProjectMemoriesPage />);

    expect(screen.getAllByText("Memoria del proyecto").length).toBeGreaterThan(0);
    expect((await screen.findByTestId("breadcrumb-link-0")).textContent).toContain("Proyectos");
    expect(await screen.findByText("Proyecto")).toBeDefined();
    expect(await screen.findByText("Semántica")).toBeDefined();
    expect(
      (await screen.findByTestId("project-memory-similar-unavailable-m-1")).textContent,
    ).toContain("No disponible aún");
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <ProjectMemoriesPage />);

    expect(screen.getAllByText("Project memory").length).toBeGreaterThan(0);
    expect((await screen.findByTestId("breadcrumb-link-0")).textContent).toContain("Projects");
    expect(await screen.findByText("Project")).toBeDefined();
    expect(await screen.findByText("Semantic")).toBeDefined();
    expect(
      (await screen.findByTestId("project-memory-similar-unavailable-m-1")).textContent,
    ).toContain("Not available yet");

    expect(screen.queryByText("Memoria del proyecto")).toBeNull();
    expect(screen.queryByText("Semántica")).toBeNull();
  });
});

describe("projects/[id]/dep-cache — caché de dependencias", () => {
  it("en castellano rinde las cabeceras de tabla y el resultado de invalidar", async () => {
    renderIn("es", <DepCachePage />);

    expect((await screen.findAllByText("Caché de dependencias")).length).toBeGreaterThan(1);
    expect(await screen.findByText("Runtimes con caché")).toBeDefined();
    expect(await screen.findByText("Punto de montaje")).toBeDefined();
    // `generic-shell` no tiene dep_cache_mount: no hay nada que invalidar.
    expect(screen.queryByTestId("invalidate-generic-shell")).toBeNull();

    fireEvent.click(await screen.findByTestId("invalidate-python-pytest"));
    await waitFor(() =>
      expect(screen.getByTestId("result-python-pytest").textContent).toBe("3 entradas invalidadas"),
    );
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <DepCachePage />);

    expect((await screen.findAllByText("Dependency cache")).length).toBeGreaterThan(1);
    expect(await screen.findByText("Runtimes with a cache")).toBeDefined();
    expect(await screen.findByText("Mount point")).toBeDefined();
    expect((await screen.findByTestId("breadcrumb-link-0")).textContent).toContain("Projects");

    fireEvent.click(await screen.findByTestId("invalidate-python-pytest"));
    await waitFor(() =>
      expect(screen.getByTestId("result-python-pytest").textContent).toBe("3 entries invalidated"),
    );

    expect(screen.queryByText("Runtimes con caché")).toBeNull();
    expect(screen.queryByText("Punto de montaje")).toBeNull();
  });
});
