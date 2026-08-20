// @vitest-environment jsdom

/**
 * `projects/[id]/mcp-servers` COMPLETO, migrado al diccionario
 * (plan prod-16, `task_prod16_03`).
 *
 * Por qué entra el módulo entero y no lo que marcaba la guarda: la
 * `ATTR_ALLOWLIST` le veía **5 atributos en 3 ficheros** (`page.tsx` 2,
 * `mcp-server-card.tsx` 2, `mcp-server-dialog.tsx` 1) de un módulo de ~1.900
 * líneas repartidas en nueve. El resto de su castellano vive donde ninguna de
 * las dos señales mira: texto JSX suelto (el panel de tools descubiertas, la
 * tarjeta de credencial gestionada, el aviso OAuth) y **tres catálogos de
 * `mcp-server-types.ts`** —`ROLE_LABEL`, `CATEGORY_LABEL`— que son un módulo
 * puro sin atributos ni ternarios.
 *
 * Y `ROLE_LABEL` tiene el mismo defecto que tuvo `MEMORY_SCOPE_OPTIONS`: lo
 * comparte `components/marketplace/deployment-config-form.tsx`, que YA estaba
 * migrado y aun así pintaba los diez roles en castellano. Migrar la constante a
 * CLAVES arregla las dos pantallas, y por eso el namespace `agentRole` es
 * compartido y no una copia por pantalla.
 *
 * Aquí se afirma la pantalla ENTERA en los dos idiomas, incluidos **el diálogo
 * con sus opciones avanzadas desplegadas** y el panel de «Probar conexión», que
 * es donde vive más de la mitad del texto y donde un `useT()` olvidado no se ve
 * hasta que alguien abre el formulario.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/mcp-servers",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import ProjectMcpServersPage from "@/app/admin/projects/[id]/mcp-servers/page";
import { McpToolRolePolicySection } from "@/app/admin/projects/[id]/mcp-servers/mcp-tool-roles-section";

const STORAGE_KEY = "admin-panel.lang";

const STDIO_SERVER = {
  name: "files",
  transport: "stdio",
  command: "npx mcp-files",
  args: [],
  env: {},
  url: null,
  headers: {},
  auth_ref: null,
  timeout_s: 30,
};

const CATALOG_ENTRY = {
  id: "github",
  display_name: "GitHub",
  description: "GitHub MCP",
  transport: "streamable_http",
  command: null,
  args: [],
  url: "https://github-mcp.example/mcp",
  secret_keys: ["GITHUB_TOKEN"],
  vault_path_template: "vault:secret/data/mcp/github/{project_id}",
  default_timeout_s: 30,
  static_env: {},
  static_headers: {},
  maintainer: "acme",
  repo_url: "",
  docs_url: "",
  category: "scm",
  requires_auth: true,
  auth_kind: "static",
};

const MCP_TOOL = {
  id: "tool-1",
  name: "files.read_file",
  description: "Lee un fichero",
  category: "mcp",
  implementation_type: "mcp_tool",
};

function project(servers: Record<string, unknown>[], toolRoles?: Record<string, string[]>) {
  return {
    id: "proj-1",
    name: "Proyecto",
    mcp_servers: servers,
    ...(toolRoles ? { mcp_tool_roles: toolRoles } : {}),
  };
}

function wireApi(
  servers: Record<string, unknown>[] = [STDIO_SERVER],
  catalog: unknown[] = [CATALOG_ENTRY],
  tools: unknown[] = [],
) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects/proj-1" && opts?.method === "PUT") {
      return Promise.resolve(project((opts.body as { mcp_servers: [] }).mcp_servers ?? []));
    }
    if (path === "/projects/proj-1") return Promise.resolve(project(servers));
    if (path === "/mcp-catalog") return Promise.resolve(catalog);
    if (path.startsWith("/tools")) return Promise.resolve(tools);
    if (path === "/projects/proj-1/mcp/test-connection" && opts?.method === "POST") {
      return Promise.resolve({
        server_name: "files-server",
        server_version: "1.2.0",
        server_instructions: null,
        tools: [
          { name: "read_file", description: "Lee un fichero" },
          { name: "list_dir", description: null },
        ],
      });
    }
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

const page = (lang: "es" | "en", ...args: Parameters<typeof wireApi>) => {
  wireApi(...args);
  return renderIn(lang, <ProjectMcpServersPage />);
};

/** Abre el diálogo de alta (el que trae el selector de plantillas). */
async function openCreateDialog(lang: "es" | "en") {
  page(lang, []);
  fireEvent.click(await screen.findByTestId("mcp-add-button"));
  await waitFor(() => expect(screen.getByTestId("mcp-server-dialog")).toBeTruthy());
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("mcp-servers en castellano", () => {
  it("rinde cabecera, acción y la ficha del server", async () => {
    page("es");

    expect(await screen.findByText("MCP servers del proyecto")).toBeDefined();
    expect(screen.getByRole("button", { name: /Añadir MCP server/ })).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("mcp-server-card-files")).toBeTruthy());
    expect(screen.getByTestId("mcp-server-edit-files").getAttribute("aria-label")).toBe("Editar");
  });

  it("rinde el estado vacío", async () => {
    page("es", []);
    const empty = await screen.findByTestId("project-mcp-empty");
    expect(empty.textContent).toContain("aún no tiene MCP servers configurados");
  });
});

describe("mcp-servers en inglés", () => {
  it("traduce cabecera, descripción y la acción", async () => {
    page("en");

    expect(await screen.findByText("Project MCP servers")).toBeDefined();
    expect(screen.getByRole("button", { name: /Add MCP server/ })).toBeDefined();
    expect(screen.queryByText("MCP servers del proyecto")).toBeNull();
    expect(screen.queryByRole("button", { name: /Añadir MCP server/ })).toBeNull();
  });

  it("traduce los aria-label de las acciones de la ficha", async () => {
    page("en");

    await waitFor(() => expect(screen.getByTestId("mcp-server-card-files")).toBeTruthy());
    expect(screen.getByTestId("mcp-server-edit-files").getAttribute("aria-label")).toBe("Edit");
    expect(screen.getByTestId("mcp-server-delete-files").getAttribute("aria-label")).toBe("Delete");
  });

  it("traduce el estado vacío", async () => {
    page("en", []);
    const empty = await screen.findByTestId("project-mcp-empty");
    expect(empty.textContent).toContain("has no MCP servers configured yet");
    expect(empty.textContent).not.toContain("aún no tiene");
  });

  it("traduce el diálogo: plantillas, campos y sus ayudas", async () => {
    await openCreateDialog("en");

    expect(screen.getByText("Configure MCP server")).toBeDefined();
    expect(screen.getByText("Quick template")).toBeDefined();
    expect(screen.getByText("— Pick a template (optional) —")).toBeDefined();
    expect(screen.getByText("Name")).toBeDefined();
    expect(screen.getByText("Transport")).toBeDefined();
    expect(screen.getByText("Command")).toBeDefined();
    expect(screen.getByText("Arguments (one per line)")).toBeDefined();
    expect(screen.getByText("Environment variables")).toBeDefined();
    expect(screen.getByTestId("mcp-form-env-empty").textContent).toContain("No variables");
    expect(screen.getByTestId("mcp-form-cancel").textContent).toBe("Cancel");

    // Y no queda el castellano por debajo.
    expect(screen.queryByText("Configurar MCP server")).toBeNull();
    expect(screen.queryByText("Plantilla rápida")).toBeNull();
    expect(screen.queryByText("Nombre")).toBeNull();
  });

  it("traduce el catálogo de categorías del selector de plantillas", async () => {
    await openCreateDialog("en");

    const select = await screen.findByTestId("mcp-form-template");
    // El nombre de la categoría vive en el ATRIBUTO `label` del `<optgroup>`,
    // no en su texto: por eso ninguna consulta por texto lo encuentra… y por eso
    // tampoco lo veía el guard de atributos, que sólo mira `label="…"` literal.
    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) =>
      g.getAttribute("label"),
    );
    // `scm` = «Control de versiones» en castellano.
    expect(groups).toContain("Version control");
    expect(groups).not.toContain("Control de versiones");
  });

  it("traduce las opciones de transporte y los campos HTTP", async () => {
    await openCreateDialog("en");

    const transport = screen.getByTestId("mcp-form-transport");
    expect(within(transport).getByText("stdio (local subprocess)")).toBeDefined();
    fireEvent.change(transport, { target: { value: "streamable_http" } });

    await waitFor(() => expect(screen.getByTestId("mcp-form-url")).toBeTruthy());
    expect(screen.getByText("Headers")).toBeDefined();
    expect(screen.getByTestId("mcp-form-headers-empty").textContent).toContain("No headers");
  });

  it("traduce las opciones avanzadas: resumen, credencial y timeout", async () => {
    await openCreateDialog("en");

    const toggle = screen.getByTestId("mcp-form-advanced-toggle");
    expect(toggle.textContent).toContain("Advanced options");
    expect(toggle.textContent).toContain("30s");
    fireEvent.click(toggle);

    await waitFor(() => expect(screen.getByTestId("mcp-form-auth-ref")).toBeTruthy());
    expect(screen.getByText("Server credential (optional)")).toBeDefined();
    expect(screen.getByTestId("mcp-form-auth-ref").getAttribute("placeholder")).toBe(
      "vault:secret/data/mcp/<service>/<project>",
    );
    expect(screen.getByText("Timeout (seconds)")).toBeDefined();
    expect(screen.queryByText("Opciones avanzadas")).toBeNull();
  });

  it("traduce la tarjeta de credencial gestionada al aplicar una plantilla", async () => {
    await openCreateDialog("en");

    fireEvent.change(screen.getByTestId("mcp-form-template"), { target: { value: "github" } });

    await waitFor(() => expect(screen.getByTestId("mcp-form-auth-managed")).toBeTruthy());
    const card = screen.getByTestId("mcp-form-auth-managed");
    expect(card.textContent).toContain("This integration requires a credential");
    expect(card.textContent).toContain("tenant administrator");
    // La clave del secreto es un dato del catálogo: no se traduce.
    expect(card.textContent).toContain("GITHUB_TOKEN");
    expect(screen.getByTestId("mcp-form-show-raw-auth").textContent).toBe("Technical details");
  });

  it("traduce «Probar conexión» y el panel de tools descubiertas", async () => {
    await openCreateDialog("en");

    expect(screen.getByText("Test connection")).toBeDefined();
    expect(screen.getByTestId("mcp-form-test").textContent).toBe("Test");

    fireEvent.change(screen.getByTestId("mcp-form-name"), { target: { value: "files" } });
    fireEvent.click(screen.getByTestId("mcp-form-test"));

    await waitFor(() => expect(screen.getByTestId("mcp-form-test-result")).toBeTruthy());
    const panel = screen.getByTestId("mcp-form-test-result");
    expect(panel.textContent).toContain("Connected to");
    // El contador sigue siendo SÓLO el número: es el ancla del spec e2e.
    expect(screen.getByTestId("mcp-form-test-tool-count").textContent).toBe("2");
    expect(screen.getByTestId("mcp-form-import-button").textContent).toBe(
      "Import 2 tools to the catalog",
    );
    expect(screen.getByTestId("mcp-form-import-select-read_file").getAttribute("aria-label")).toBe(
      "Select read_file",
    );
  });

  it("traduce el aviso OAuth del diálogo", async () => {
    wireApi([], [{ ...CATALOG_ENTRY, id: "atlassian", auth_kind: "oauth", requires_auth: false }]);
    renderIn("en", <ProjectMcpServersPage />);
    fireEvent.click(await screen.findByTestId("mcp-add-button"));
    await waitFor(() => expect(screen.getByTestId("mcp-form-template")).toBeTruthy());
    fireEvent.change(screen.getByTestId("mcp-form-template"), { target: { value: "atlassian" } });

    await waitFor(() => expect(screen.getByTestId("mcp-form-oauth-note")).toBeTruthy());
    const note = screen.getByTestId("mcp-form-oauth-note");
    expect(note.textContent).toContain("This server connects over OAuth");
    expect(note.textContent).toContain("You do not need to paste any token");
    expect(note.textContent).not.toContain("No necesitas pegar");
  });

  it("traduce la ficha «Conexión OAuth» de un server ya guardado", async () => {
    const oauthServer = {
      ...STDIO_SERVER,
      name: "atlassian",
      transport: "streamable_http",
      command: null,
      url: "https://github-mcp.example/mcp",
    };
    page("en", [oauthServer], [{ ...CATALOG_ENTRY, auth_kind: "oauth" }]);

    await waitFor(() => expect(screen.getByTestId("mcp-oauth-connect-atlassian")).toBeTruthy());
    const panel = screen.getByTestId("mcp-oauth-connect-atlassian");
    expect(panel.textContent).toContain("OAuth connection");
    expect(panel.textContent).toContain("Authorize access to GitHub once");
    expect(screen.getByTestId("mcp-oauth-connect-button-atlassian").textContent).toContain(
      "Connect",
    );
    expect(panel.textContent).not.toContain("Conexión OAuth");
  });

  it("traduce el banner de vuelta del consentimiento OAuth", async () => {
    const nav = await import("next/navigation");
    vi.spyOn(nav, "useSearchParams").mockReturnValue(
      new URLSearchParams("oauth_result=connected&server=atlassian") as never,
    );

    page("en");
    const banner = await screen.findByTestId("mcp-oauth-banner-connected");
    expect(banner.textContent).toContain("OAuth connection completed for");
    expect(banner.textContent).toContain("atlassian");
    vi.restoreAllMocks();
  });
});

describe("política rol→tool en los dos idiomas", () => {
  const roles = (lang: "es" | "en") => {
    wireApi([STDIO_SERVER], [CATALOG_ENTRY], [MCP_TOOL]);
    return renderIn(lang, <McpToolRolePolicySection projectId="proj-1" />);
  };

  it("rinde el bloque en castellano", async () => {
    roles("es");

    expect(await screen.findByText(/Acceso por rol a las tools MCP/)).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("mcp-tool-roles-list")).toBeTruthy());
    expect(screen.getByTestId("mcp-tool-roles-open-files.read_file").textContent).toBe(
      "Abierta a todos",
    );
    // El rol traducido: en castellano «Arquitecto».
    expect(screen.getByText("Arquitecto")).toBeDefined();
  });

  it("traduce cabecera, badges y los diez roles del catálogo compartido", async () => {
    roles("en");

    expect(await screen.findByText(/Role-based access to MCP tools/)).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("mcp-tool-roles-list")).toBeTruthy());
    expect(screen.getByTestId("mcp-tool-roles-open-files.read_file").textContent).toBe(
      "Open to all",
    );
    expect(screen.getByText("Architect")).toBeDefined();
    expect(screen.getByText("Specialist")).toBeDefined();
    expect(screen.queryByText("Arquitecto")).toBeNull();
    expect(screen.queryByText("Especialista")).toBeNull();
    expect(screen.queryByText("Abierta a todos")).toBeNull();

    expect(
      screen
        .getByTestId("mcp-tool-roles-role-files.read_file-architect")
        .getAttribute("aria-label"),
    ).toBe("Architect can use files.read_file");
  });

  it("traduce el estado vacío y los botones de guardado", async () => {
    wireApi([STDIO_SERVER], [CATALOG_ENTRY], []);
    renderIn("en", <McpToolRolePolicySection projectId="proj-1" />);

    const empty = await screen.findByTestId("mcp-tool-roles-empty");
    expect(empty.textContent).toContain("no MCP tools imported yet");
    expect(screen.getByTestId("mcp-tool-roles-save").textContent).toBe("Save");
  });
});
