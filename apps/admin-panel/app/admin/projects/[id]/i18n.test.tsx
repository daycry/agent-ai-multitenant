// @vitest-environment jsdom

/**
 * El HUB del proyecto en los dos idiomas (plan prod-16, `task_prod16_03`).
 *
 * El hub es la pantalla que la nota del plan dejaba fuera «porque no entra a
 * trozos»: `app/admin/projects/[id]/page.tsx` reparte su texto entre SEIS
 * piezas de `components/projects/` —git, servicios de runtime, gobierno,
 * app-preview de review, lanzador de preview— y `lib/project-governance.ts`,
 * un módulo PURO donde ninguna de las dos guardas de `check-i18n` mira. Migrar
 * sólo el marco habría dado la pantalla mitad-y-mitad que este plan cierra.
 *
 * Por eso casi todos los casos rinden **la página entera**, no las piezas
 * sueltas: es el único render que demuestra que las seis secciones traducen a
 * la vez. Los que sí montan una pieza aislada son los que necesitan interacción
 * (un modo de auth de git, una fila de servicio, un periodo personalizado) o un
 * estado que la página no alcanza sola (un `last_git_sync` divergido).
 *
 * Cada caso afirma en los DOS sentidos: en inglés, que sale el texto inglés Y
 * que NO queda su cara castellana. Sin la segunda mitad un `useT()` olvidado
 * pasa desapercibido, porque el resto de la pantalla sí traduce.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

import ProjectHubPage from "@/app/admin/projects/[id]/page";
import { GitConfigSection } from "@/components/projects/git-config-section";
import { PreviewLauncher } from "@/components/projects/preview-launcher";
import { ProjectGovernanceSection } from "@/components/projects/governance-section";
import { RuntimeServicesSection } from "@/components/projects/runtime-services-section";

const STORAGE_KEY = "admin-panel.lang";
const PROJECT_ID = "11111111-2222-3333-4444-555555555555";

const GIT_CONFIG = {
  provider: "github",
  remote_url: "https://github.com/acme/demo.git",
  default_branch: "main",
  auth_mode: "none",
};

const PROJECT = {
  id: PROJECT_ID,
  name: "Demo",
  description: "Un proyecto de prueba.",
  status: "active",
  team_id: null,
  is_template: false,
  model_config: {},
  chat_model_config: {},
  git_config: GIT_CONFIG,
  worker_config: {},
  repository_config: {},
};

const CAPABILITIES = {
  entity_type: "project",
  entity_id: PROJECT_ID,
  saber: { knowledge_bases: [] },
  recordar: { memory_scope: null, memory: [] },
  ser: null,
  hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
  warnings: [],
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
  if (path === `/projects/${PROJECT_ID}`) return PROJECT;
  if (path === `/projects/${PROJECT_ID}/capabilities`) return CAPABILITIES;
  if (path === "/agents/provider-options") return { providers: [] };
  if (path === "/teams") return [{ id: "team-1", name: "Plataforma" }];
  if (path === `/projects/${PROJECT_ID}/preview-session`) return null;
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

/** La página entera, esperando a que el proyecto haya cargado. */
async function renderHub(lang: "es" | "en") {
  renderIn(lang, <ProjectHubPage />);
  await screen.findByTestId("project-status-row");
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("projects/[id] — el marco del hub", () => {
  it("en castellano rinde cabecera, acciones, estado y la rejilla de secciones", async () => {
    await renderHub("es");

    expect((await screen.findByTestId("project-edit-button")).textContent).toContain("Editar");
    expect(screen.getByTestId("project-delete-button").textContent).toContain("Borrar");
    expect(screen.getByTestId("project-status-row").textContent).toContain("Estado:");
    expect(screen.getByText("Secciones")).toBeDefined();
    expect(screen.getByTestId("project-section-plans").textContent).toContain("Planes");
    expect(screen.getByTestId("project-section-memories").textContent).toContain("Memoria");
    expect(screen.getByTestId("project-section-dep-cache").textContent).toContain(
      "Caché de dependencias",
    );
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    await renderHub("en");

    expect((await screen.findByTestId("project-edit-button")).textContent).toContain("Edit");
    expect(screen.getByTestId("project-delete-button").textContent).toContain("Delete");
    expect(screen.getByTestId("project-status-row").textContent).toContain("Status:");
    expect(screen.getByText("Sections")).toBeDefined();
    expect(screen.getByTestId("project-section-plans").textContent).toContain("Plans");
    expect(screen.getByTestId("project-section-memories").textContent).toContain("Memory");
    expect(screen.getByTestId("project-section-dep-cache").textContent).toContain(
      "Dependency cache",
    );

    expect(screen.queryByText("Secciones")).toBeNull();
    expect(screen.queryByText("Caché de dependencias")).toBeNull();
    expect(screen.getByTestId("project-status-row").textContent).not.toContain("Estado:");
  });

  it("el error de carga se traduce y NO pinta el cuerpo crudo del backend", async () => {
    const { ApiError } = await import("@/lib/api");
    apiFetchMock.mockImplementation((path: string) => {
      if (path === `/projects/${PROJECT_ID}`) {
        return Promise.reject(new ApiError(500, "<html>nginx traceback</html>"));
      }
      return Promise.resolve(routeApi(path));
    });
    window.localStorage.setItem(STORAGE_KEY, "en");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <ProjectHubPage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    const card = await screen.findByTestId("project-error");
    expect(card.textContent).toContain("Could not load the project");
    expect(card.textContent).not.toContain("nginx");
    expect(screen.getByRole("link", { name: "Back to the list" })).toBeDefined();
  });
});

describe("projects/[id] — los dos diálogos", () => {
  it("el diálogo de edición se traduce entero, incluidos el editor markdown y los estados", async () => {
    await renderHub("en");

    fireEvent.click(screen.getByTestId("project-edit-button"));

    expect(await screen.findByText("Edit project")).toBeDefined();
    expect(screen.getByText("Name")).toBeDefined();
    expect(screen.getByText("Team")).toBeDefined();
    expect(await screen.findByText("No team")).toBeDefined();
    // Los tres estados viven en un catálogo del propio fichero.
    expect(screen.getByText("Paused")).toBeDefined();
    // El editor markdown es un componente COMPARTIDO: su barra de pestañas
    // seguía en castellano en las 22 pantallas que lo montan.
    expect(screen.getByTestId("edit-project-description-tab-preview").textContent).toContain(
      "Preview",
    );
    expect(screen.getByTestId("edit-project-save").textContent).toContain("Save");

    expect(screen.queryByText("Editar proyecto")).toBeNull();
    expect(screen.queryByText("Sin equipo")).toBeNull();
    expect(screen.queryByText("Pausado")).toBeNull();
    expect(screen.queryByText("Vista previa")).toBeNull();
  });

  it("el diálogo de borrado —el que confirma por nombre— se traduce entero", async () => {
    await renderHub("en");

    fireEvent.click(screen.getByTestId("project-delete-button"));

    expect(await screen.findByText("Delete project")).toBeDefined();
    expect(screen.getByText(/To confirm, type the project name/)).toBeDefined();
    expect(screen.getByTestId("delete-project-confirm").textContent).toContain(
      "Delete permanently",
    );

    expect(screen.queryByText("Borrar proyecto")).toBeNull();
    expect(screen.queryByText("Borrar definitivamente")).toBeNull();
  });
});

describe("components/projects — las seis piezas dentro del hub", () => {
  it("en inglés traducen las seis a la vez (es lo que no se puede comprobar por trozos)", async () => {
    await renderHub("en");

    // git-config-section
    expect(screen.getByText("Git repository")).toBeDefined();
    expect(screen.getByText("Default branch")).toBeDefined();
    expect(screen.getByText("Plan git flow")).toBeDefined();
    expect(screen.getByTestId("git-save").textContent).toContain("Save repository");
    // review-preview-section
    expect(screen.getByText("Human-validation app preview")).toBeDefined();
    expect(screen.getByTestId("review-preview-save").textContent).toContain("Save app preview");
    // runtime-services-section
    expect(screen.getByText("Backing services and runtime image")).toBeDefined();
    expect(screen.getByText("No services declared.")).toBeDefined();
    expect(screen.getByTestId("add-env").textContent).toContain("Add variable");
    // governance-section
    expect(screen.getByText("Project limits and governance")).toBeDefined();
    expect(screen.getByText("Tokens per run")).toBeDefined();
    expect(screen.getByTestId("governance-save").textContent).toContain("Save limits");
    // preview-launcher
    expect(screen.getByText("App preview (project)")).toBeDefined();
    expect(screen.getByTestId("preview-launch").textContent).toContain("Launch preview");

    expect(screen.queryByText("Repositorio Git")).toBeNull();
    expect(screen.queryByText("Flujo git del plan")).toBeNull();
    expect(screen.queryByText("App-preview de validación humana")).toBeNull();
    expect(screen.queryByText("Servicios e imagen de runtime")).toBeNull();
    expect(screen.queryByText("Límites y gobierno del proyecto")).toBeNull();
    expect(screen.queryByText("Sin servicios declarados.")).toBeNull();
  });

  it("en castellano siguen saliendo las seis en castellano (no se rompe el idioma por defecto)", async () => {
    await renderHub("es");

    expect(screen.getByText("Repositorio Git")).toBeDefined();
    expect(screen.getByText("Flujo git del plan")).toBeDefined();
    expect(screen.getByText("App-preview de validación humana")).toBeDefined();
    expect(screen.getByText("Servicios e imagen de runtime")).toBeDefined();
    expect(screen.getByText("Límites y gobierno del proyecto")).toBeDefined();
    expect(screen.getByText("Tokens por run")).toBeDefined();
    expect(screen.getByText("Preview de la app (proyecto)")).toBeDefined();
  });
});

describe("git-config-section — lo que sólo se ve interactuando", () => {
  function renderGit(lang: "es" | "en", extra: Record<string, unknown> = {}) {
    return renderIn(
      lang,
      <GitConfigSection projectId={PROJECT_ID} value={GIT_CONFIG} policies={null} {...extra} />,
    );
  }

  it("los campos de PAT y de clave SSH —plegados por defecto— también se traducen", () => {
    renderGit("en");

    fireEvent.change(screen.getByTestId("git-auth-mode"), { target: { value: "pat" } });
    expect(screen.getByText("Username (optional)")).toBeDefined();
    expect(screen.getByText("Token (PAT)")).toBeDefined();
    expect((screen.getByTestId("git-token") as HTMLInputElement).placeholder).toContain("keep");

    fireEvent.change(screen.getByTestId("git-auth-mode"), { target: { value: "ssh" } });
    expect(screen.getByText("Private SSH key")).toBeDefined();

    expect(screen.queryByText("Usuario (opcional)")).toBeNull();
    expect(screen.queryByText("Clave SSH privada")).toBeNull();
  });

  it("el aviso de rama divergida —el que explica un PR fallido— se traduce", () => {
    renderGit("en", {
      lastSync: {
        at: "2026-08-20T10:00:00Z",
        status: "error",
        default_branch_alignment: "diverged",
        error: "fatal: refusing to merge unrelated histories",
      },
    });

    const box = screen.getByTestId("git-last-sync");
    expect(box.textContent).toContain("Last sync:");
    expect(box.textContent).toContain("failed");
    expect(screen.getByTestId("git-alignment").textContent).toContain("no history in common");

    expect(box.textContent).not.toContain("Última sincronización");
    expect(box.textContent).not.toContain("con error");
  });

  it("en castellano el mismo aviso sigue en castellano", () => {
    renderGit("es", {
      lastSync: { at: "2026-08-20T10:00:00Z", status: "ok", default_branch_alignment: "diverged" },
    });

    expect(screen.getByTestId("git-last-sync").textContent).toContain("Última sincronización");
    expect(screen.getByTestId("git-alignment").textContent).toContain("NO comparte historia");
  });
});

describe("runtime-services-section — el catálogo y las validaciones", () => {
  function renderServices(lang: "es" | "en") {
    return renderIn(lang, <RuntimeServicesSection projectId={PROJECT_ID} value={null} />);
  }

  it("la fila de servicio y sus etiquetas accesibles se traducen", () => {
    renderServices("en");

    fireEvent.click(screen.getByTestId("add-service"));

    expect(screen.getByLabelText("Service type")).toBeDefined();
    expect(screen.getByLabelText("Alias (hostname)")).toBeDefined();
    // La escotilla de imagen es la única opción del <select> que NO es un
    // identificador del catálogo, así que es la única que se traduce.
    expect(screen.getByText("image…")).toBeDefined();
    expect(screen.queryByText("imagen…")).toBeNull();
    expect(screen.queryByLabelText("Tipo de servicio")).toBeNull();
  });

  it("los mensajes de validación viven en el módulo y se traducen (inglés)", () => {
    renderServices("en");

    fireEvent.click(screen.getByTestId("add-service"));
    fireEvent.change(screen.getByTestId("service-alias-0"), { target: { value: "MalAlias" } });

    expect(screen.getByTestId("runtime-services-validation").textContent).toContain(
      "Invalid alias: MalAlias",
    );
    expect((screen.getByTestId("runtime-services-save") as HTMLButtonElement).disabled).toBe(true);
  });

  it("y en castellano dicen lo mismo en castellano", () => {
    renderServices("es");

    fireEvent.click(screen.getByTestId("add-service"));
    fireEvent.change(screen.getByTestId("service-alias-0"), { target: { value: "MalAlias" } });

    expect(screen.getByTestId("runtime-services-validation").textContent).toContain(
      "Alias inválido: MalAlias",
    );
  });
});

describe("governance-section — el catálogo y los problemas del módulo puro", () => {
  function renderGovernance(lang: "es" | "en") {
    return renderIn(lang, <ProjectGovernanceSection projectId={PROJECT_ID} value={null} />);
  }

  it("los dos modos de revisión humana y su explicación se traducen", () => {
    renderGovernance("en");

    const select = screen.getByTestId("human-task-review-mode");
    expect(within(select).getByText("Auto-approve on submit")).toBeDefined();
    expect(within(select).getByText("Review by another person")).toBeDefined();
    // La explicación del modo activo sale del mismo catálogo.
    expect(screen.getByText(/Submitting the task marks it done/)).toBeDefined();

    expect(screen.queryByText("Auto-aprobar al entregar")).toBeNull();
  });

  it("el periodo personalizado descubre dos campos más, y también se traducen", () => {
    renderGovernance("en");

    fireEvent.change(screen.getByTestId("budget-period"), { target: { value: "custom" } });

    expect(screen.getByText("Period start day")).toBeDefined();
    expect(screen.getByText("Length (days)")).toBeDefined();
    expect(screen.queryByText("Día de inicio del periodo")).toBeNull();
  });

  it("los problemas los redacta `lib/project-governance.ts`, y se traducen (inglés)", () => {
    renderGovernance("en");

    fireEvent.change(screen.getByTestId("exec-budget-max_tokens"), { target: { value: "0" } });

    expect(screen.getByTestId("governance-problems").textContent).toContain(
      "«Tokens per run» must be greater than zero.",
    );
    expect((screen.getByTestId("governance-save") as HTMLButtonElement).disabled).toBe(true);
  });

  it("y en castellano siguen diciendo lo mismo en castellano", () => {
    renderGovernance("es");

    fireEvent.change(screen.getByTestId("guardrails-config"), { target: { value: "{nope" } });

    expect(screen.getByTestId("governance-problems").textContent).toContain(
      "Los guardrails no son JSON válido.",
    );
  });
});

describe("preview-launcher — la pieza que comparten el hub y la ficha del plan", () => {
  it("dice de qué rama levanta la app, y lo dice distinto en cada scope", async () => {
    apiFetchMock.mockImplementation(() => Promise.resolve(null));
    window.localStorage.setItem(STORAGE_KEY, "en");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <PreviewLauncher scope="plans" id="plan-1" />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("App preview (this plan)")).toBeDefined();
    expect(screen.getByText(/from this plan's branch/)).toBeDefined();
    expect(screen.queryByText("Preview de la app (este plan)")).toBeNull();
  });

  it("con una sesión viva ofrece abrir la app y relanzar, traducidos", async () => {
    apiFetchMock.mockImplementation(() =>
      Promise.resolve({
        session_id: "s-1",
        status: "running",
        app_url: "https://preview.example/app",
        expires_at: "2026-08-21T10:00:00Z",
        app_configured: true,
      }),
    );
    window.localStorage.setItem(STORAGE_KEY, "en");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <PreviewLauncher scope="projects" id={PROJECT_ID} />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("preview-open-app")).toBeDefined());
    expect(screen.getByTestId("preview-open-app").textContent).toContain("Open app");
    expect(screen.getByTestId("preview-launch").textContent).toContain("Relaunch preview");
    expect(screen.getByText(/Expires:/)).toBeDefined();

    expect(screen.queryByText("Relanzar preview")).toBeNull();
  });
});
