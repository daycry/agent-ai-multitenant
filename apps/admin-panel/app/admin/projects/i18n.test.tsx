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
import NewProjectWizardPage from "@/app/admin/projects/new/page";
import ProjectCommandsPage from "@/app/admin/projects/[id]/commands/page";
import ProjectKnowledgeBasesPage from "@/app/admin/projects/[id]/knowledge-bases/page";
import IncomingWebhooksPage from "@/app/admin/projects/[id]/incoming-webhooks/page";
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
  allowed_commands: ["php"],
  allowed_domains: [],
  default_runtime_template: null,
};

const TEMPLATE = {
  id: "tpl-1",
  name: "Plantilla: CodeIgniter 4",
  description: "Equipo CI4 listo para usar.",
  status: "active",
  team_id: "team-1",
  is_template: true,
  worker_config: {},
  repository_config: null,
  human_approval_policy: null,
  allowed_commands: [],
  allowed_domains: [],
  default_runtime_template: null,
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
  if (path === "/projects?include_templates=true") return [PROJECT, TEMPLATE];
  if (path === "/teams") return [{ id: "team-1", name: "Plataforma" }];
  if (path === "/marketplace/installations?limit=500") return [];
  if (path === "/marketplace/listings?limit=500") return [];
  if (path === "/knowledge-bases" || path === `/projects/${PROJECT_ID}/knowledge-bases`) {
    return [
      {
        id: "kb-1",
        tenant_id: "t-1",
        name: "Manuales",
        description: null,
        embedding_model_id: "bge-m3",
        platform_embedding_model: "bge-m3",
        embedding_model_stale: false,
        created_by: null,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
      },
    ];
  }
  if (path === "/knowledge-bases/kb-1/documents") {
    return [
      {
        id: "doc-1",
        kb_id: "kb-1",
        title: "Guía de despliegue",
        source_filename: "guia.pdf",
        source_mime_type: "application/pdf",
        source_size_bytes: 2048,
        status: "indexed",
        error_message: null,
        page_count: 4,
        indexed_at: "2026-08-20T00:00:00Z",
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
      },
    ];
  }
  if (path === `/projects/${PROJECT_ID}/incoming-webhooks`) {
    return [
      {
        id: "wh-1",
        project_id: PROJECT_ID,
        origin: "github",
        name: "CI",
        enabled: true,
        action_mappings: [],
        last_event_at: null,
        created_at: "2026-08-20T00:00:00Z",
        updated_at: "2026-08-20T00:00:00Z",
        incoming_path: "/webhooks/in/github/abc",
      },
    ];
  }
  if (path === `/projects/${PROJECT_ID}/incoming-webhooks/wh-1/deliveries`) {
    return [
      {
        id: "dlv-1",
        origin: "github",
        delivery_id: "d-1",
        event_type: "github.push",
        verified: true,
        received_at: "2026-08-20T10:00:00Z",
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

describe("projects/[id]/incoming-webhooks — webhooks entrantes", () => {
  it("en castellano rinde cabecera, ficha y el desplegable de entregas", async () => {
    renderIn("es", <IncomingWebhooksPage />);

    expect(await screen.findByText("Webhooks entrantes del proyecto")).toBeDefined();
    expect((await screen.findByTestId("webhook-add-button")).textContent).toContain(
      "Añadir webhook",
    );
    expect((await screen.findByTestId("webhook-enabled-wh-1")).textContent).toContain("activo");
    expect((await screen.findByTestId("webhook-deliveries-toggle-wh-1")).textContent).toBe(
      "Ver entregas recientes",
    );
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <IncomingWebhooksPage />);

    expect(await screen.findByText("Project incoming webhooks")).toBeDefined();
    expect(
      screen.getByText(
        "Events that external tools (GitHub, Jira, Sentry…) send to this project. The HMAC signature is verified before acting.",
      ),
    ).toBeDefined();
    expect((await screen.findByTestId("webhook-add-button")).textContent).toContain("Add webhook");
    expect((await screen.findByTestId("breadcrumb-link-0")).textContent).toContain("Projects");
    expect((await screen.findByTestId("webhook-enabled-wh-1")).textContent).toContain("active");
    expect((await screen.findByTestId("webhook-deliveries-toggle-wh-1")).textContent).toBe(
      "Show recent deliveries",
    );

    expect(screen.queryByText("Webhooks entrantes del proyecto")).toBeNull();
    expect(screen.queryByText("Añadir webhook")).toBeNull();
  });

  it("el panel de entregas —plegado por defecto— también se traduce", async () => {
    renderIn("en", <IncomingWebhooksPage />);

    fireEvent.click(await screen.findByTestId("webhook-deliveries-toggle-wh-1"));

    expect((await screen.findByTestId("webhook-delivery-dlv-1")).textContent).toContain("verified");
    expect(screen.getByTestId("webhook-deliveries-toggle-wh-1").textContent).toBe(
      "Hide recent deliveries",
    );
    expect(screen.queryByText("verificado")).toBeNull();
  });

  it("el diálogo de alta se traduce entero, incluidos los catálogos de origen y acción", async () => {
    renderIn("en", <IncomingWebhooksPage />);

    fireEvent.click(await screen.findByTestId("webhook-add-button"));

    expect(await screen.findByText("New incoming webhook")).toBeDefined();
    expect(screen.getByText("Origin")).toBeDefined();
    expect(screen.getByText("Event → action mappings")).toBeDefined();
    // El catálogo de orígenes tiene una entrada que NO es un nombre propio.
    expect(screen.getByText("Generic (bare-hex HMAC)")).toBeDefined();
    expect(screen.getByTestId("webhook-form-rules-empty").textContent).toContain("No mappings");
    expect(screen.getByTestId("webhook-form-submit").textContent).toContain("Create");

    // El catálogo de acciones vive dentro de una regla: hay que crear una.
    fireEvent.click(screen.getByTestId("webhook-form-add-rule"));
    expect(await screen.findByText("Create task")).toBeDefined();

    expect(screen.queryByText("Genérico (HMAC bare-hex)")).toBeNull();
    expect(screen.queryByText("Crear tarea")).toBeNull();
    expect(screen.queryByText("Origen")).toBeNull();
  });
});

describe("projects/[id]/commands — comandos & runtime", () => {
  it("en castellano rinde las dos allowlists, los presets y el runtime", async () => {
    renderIn("es", <ProjectCommandsPage />);

    expect(await screen.findByText("Comandos autorizados")).toBeDefined();
    expect((await screen.findByTestId("commands-privileged-badge")).textContent).toContain(
      "Privilegiada",
    );
    expect((await screen.findByTestId("commands-preset-read")).textContent).toContain("Lectura");
    expect(screen.getByText("Dominios de red autorizados")).toBeDefined();
    expect((await screen.findByTestId("domains-empty")).textContent).toContain(
      "Sin dominios autorizados",
    );
    expect(screen.getByText("Runtime por defecto")).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <ProjectCommandsPage />);

    expect(await screen.findByText("Allowed commands")).toBeDefined();
    expect((await screen.findByTestId("commands-privileged-badge")).textContent).toContain(
      "Privileged",
    );
    // El preset que NO es un nombre propio: los otros cuatro son marcas.
    expect((await screen.findByTestId("commands-preset-read")).textContent).toContain("Read-only");
    expect(screen.getByText("Allowed network domains")).toBeDefined();
    expect((await screen.findByTestId("domains-empty")).textContent).toContain(
      "No allowed domains",
    );
    expect(screen.getByText("Default runtime")).toBeDefined();
    expect(screen.getByLabelText("Remove php")).toBeDefined();
    expect((await screen.findByTestId("commands-save-button")).textContent).toContain(
      "Save changes",
    );

    expect(screen.queryByText("Comandos autorizados")).toBeNull();
    expect(screen.queryByText("Dominios de red autorizados")).toBeNull();
    expect(screen.queryByLabelText("Quitar php")).toBeNull();
  });

  it("el selector de runtime traduce su opción vacía, que es la que sale por defecto", async () => {
    renderIn("en", <ProjectCommandsPage />);

    expect(await screen.findByText("— No default runtime (per-tool defaults) —")).toBeDefined();
    expect(screen.queryByText("— Sin runtime por defecto (defaults por-tool) —")).toBeNull();
  });
});

describe("projects/[id]/knowledge-bases — KBs del proyecto", () => {
  it("en castellano rinde cabecera, catálogo, ficha y estado del documento", async () => {
    renderIn("es", <ProjectKnowledgeBasesPage />);

    expect(await screen.findByText("Knowledge Bases del proyecto")).toBeDefined();
    expect((await screen.findByTestId("add-knowledge-button")).textContent).toContain(
      "Añadir conocimiento",
    );
    expect(await screen.findByText("Catálogo de conocimiento")).toBeDefined();
    expect((await screen.findByTestId("kb-doc-status-doc-1")).textContent).toBe("Indexado");
    expect((await screen.findByTestId("kb-catalog-toggle-kb-1")).textContent).toContain(
      "Desactivar",
    );
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <ProjectKnowledgeBasesPage />);

    expect(await screen.findByText("Project knowledge bases")).toBeDefined();
    expect((await screen.findByTestId("add-knowledge-button")).textContent).toContain(
      "Add knowledge",
    );
    expect(await screen.findByText("Knowledge catalog")).toBeDefined();
    expect((await screen.findByTestId("kb-doc-status-doc-1")).textContent).toBe("Indexed");
    expect((await screen.findByTestId("kb-catalog-toggle-kb-1")).textContent).toContain("Disable");
    expect((await screen.findByTestId("kb-doc-ingestion-link-doc-1")).textContent).toBe("Progress");

    expect(screen.queryByText("Catálogo de conocimiento")).toBeNull();
    expect(screen.queryByText("Indexado")).toBeNull();
  });

  it("el diálogo de subida se traduce, y el nombre de la KB implícita NO (es un dato)", async () => {
    renderIn("en", <ProjectKnowledgeBasesPage />);

    // El nombre de la KB implícita se persiste y sirve de clave del
    // find-or-create: traducirlo crearía una KB distinta por idioma.
    expect(await screen.findByText(/“Documentos de Demo” KB/)).toBeDefined();

    fireEvent.click(await screen.findByTestId("kb-upload-open-kb-1"));

    expect(await screen.findByText("Upload a document to the KB")).toBeDefined();
    expect(screen.getByText("Title (optional)")).toBeDefined();
    expect(screen.getByTestId("kb-upload-submit").textContent).toContain("Upload");
    expect(screen.getByTestId("kb-upload-cancel").textContent).toContain("Cancel");

    expect(screen.queryByText("Título (opcional)")).toBeNull();
    expect(screen.queryByText("Subir documento a la KB")).toBeNull();
  });
});

describe("projects/new — wizard de alta", () => {
  it("en castellano rinde el paso 1, el proyecto en blanco y la plantilla", async () => {
    renderIn("es", <NewProjectWizardPage />);

    expect((await screen.findByTestId("wizard-title")).textContent).toBe(
      "Crear proyecto — elige plantilla",
    );
    expect(screen.getByText("Paso 1 de 2.")).toBeDefined();
    expect(screen.getByText("Proyecto en blanco")).toBeDefined();
    expect((await screen.findByTestId("wizard-blank-project-pick")).textContent).toContain(
      "Empezar en blanco",
    );
    expect((await screen.findByTestId("template-pick-tpl-1")).textContent).toContain(
      "Usar plantilla",
    );
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <NewProjectWizardPage />);

    expect((await screen.findByTestId("wizard-title")).textContent).toBe(
      "New project — pick a template",
    );
    expect(screen.getByText("Step 1 of 2.")).toBeDefined();
    expect(screen.getByText("Blank project")).toBeDefined();
    expect((await screen.findByTestId("wizard-blank-project-pick")).textContent).toContain(
      "Start blank",
    );
    expect((await screen.findByTestId("template-pick-tpl-1")).textContent).toContain(
      "Use template",
    );

    expect(screen.queryByText("Proyecto en blanco")).toBeNull();
    expect(screen.queryByText("Paso 1 de 2.")).toBeNull();
  });

  it("el paso 2 —donde se rellena el formulario— también se traduce entero", async () => {
    renderIn("en", <NewProjectWizardPage />);

    fireEvent.click(await screen.findByTestId("wizard-blank-project-pick"));

    expect((await screen.findByTestId("wizard-title")).textContent).toBe("New project — customise");
    expect(screen.getByText("Project details")).toBeDefined();
    expect(screen.getByText("Default runtime")).toBeDefined();
    // Proyecto en blanco: el selector de equipo, con su opción vacía.
    expect(screen.getByText("No team")).toBeDefined();
    expect(await screen.findByText("— No default runtime (per-tool defaults) —")).toBeDefined();
    expect(screen.getByTestId("wizard-submit").textContent).toContain("Create project");
    expect(screen.getByTestId("wizard-back").textContent).toContain("Back");

    expect(screen.queryByText("Detalles del proyecto")).toBeNull();
    expect(screen.queryByText("Sin equipo")).toBeNull();
    expect(screen.queryByText("Crear proyecto")).toBeNull();
  });
});
