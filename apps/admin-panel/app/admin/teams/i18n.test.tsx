// @vitest-environment jsdom

/**
 * El módulo `teams` entero, migrado al diccionario (prod-16 `task_prod16_04`).
 *
 * Siete ficheros: la lista, el detalle y los cinco diálogos (alta de miembro,
 * edición de miembro, edición del equipo, borrado y adopción de un built-in).
 * Entran TODOS los diálogos a propósito: es donde vive más de la mitad del texto
 * y donde un `useT()` olvidado no se ve hasta que alguien pulsa el botón.
 *
 * `components/teams/adopt-team-dialog.tsx` era además el último de los cuatro
 * ficheros con ternario de idioma del panel — y de los tramposos: no tenía
 * ternarios sueltos sino un `const t = (es, en) => …` local, o sea un
 * diccionario privado de fichero con veinte textos que la guarda contaba como
 * UNO.
 *
 * ## El hueco que esta cabecera anotaba está CERRADO (2026-08-19)
 *
 * Decía: «el `<Select>` de política de memoria pinta `MEMORY_SCOPE_OPTIONS` de
 * `lib/memory/constants.ts`, que sólo tiene etiquetas en castellano y lo comparte
 * la ficha del agente; con el toggle en EN esas cuatro opciones siguen en
 * castellano». La constante guarda ahora la CLAVE del diccionario en vez del
 * texto, y sus DOS consumidores la resuelven con el idioma activo. Lo cubre el
 * caso «las opciones de política de memoria también se traducen» de más abajo,
 * más `lib/memory/constants.test.ts`, que lo vigila en la fuente única.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: () => ({ team_id: "team-1" }),
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/teams",
  useSearchParams: () => new URLSearchParams(),
}));

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

import TeamDetailPage from "@/app/admin/teams/[team_id]/page";
import TeamsListPage from "@/app/admin/teams/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const BUILTIN_TEAM = {
  id: "team-b",
  tenant_id: "t1",
  name: "Equipo built-in",
  description: null,
  members: [],
  is_builtin: true,
};

const OWN_TEAM = {
  id: "team-1",
  tenant_id: "t1",
  name: "Equipo Backend",
  description: "El de siempre",
  members: [
    { agent_id: "a1", role_in_team: "Tech Lead", is_team_leader: true, assignment_priority: 10 },
  ],
  is_builtin: false,
  model_config: null,
  chat_model_config: null,
  memory_scope: null,
};

const AGENTS = [
  { id: "a1", name: "Ada", role: "backend_dev", scope: "global_tenant_template" },
  { id: "a2", name: "Linus", role: "qa", scope: "global_tenant_template" },
];

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/teams") return Promise.resolve([BUILTIN_TEAM, OWN_TEAM]);
    if (path === "/teams/team-1") return Promise.resolve(OWN_TEAM);
    if (path === "/agents") return Promise.resolve(AGENTS);
    if (path === "/projects") return Promise.resolve([]);
    if (path.startsWith("/teams/team-1/capabilities")) {
      // El `CapabilityHub` del detalle revienta el árbol entero si esto no trae
      // la forma real (`caps.saber.knowledge_bases`), y el fallo se lee como si
      // lo hubiera roto la migración.
      return Promise.resolve({
        entity_type: "team",
        entity_id: "team-1",
        saber: { knowledge_bases: [] },
        recordar: { memory_scope: null, memory: [] },
        ser: null,
        hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
        warnings: [],
      });
    }
    return Promise.resolve([]);
  });
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  wireApi();
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
  pushMock.mockReset();
  window.localStorage.clear();
});

describe("lista de equipos", () => {
  it("en castellano rinde cabecera, pestañas y las tarjetas", async () => {
    renderIn("es", <TeamsListPage />);

    expect(await screen.findByText("Equipos")).toBeDefined();
    expect((await screen.findByTestId("tab-template")).textContent).toContain(
      "Plantillas del Tenant",
    );
    expect(screen.getByTestId("tab-local").textContent).toContain("Locales del Proyecto");
    expect((await screen.findByTestId("team-team-b")).textContent).toContain("Ver detalle");
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <TeamsListPage />);

    expect(await screen.findByText("Teams")).toBeDefined();
    expect((await screen.findByTestId("tab-template")).textContent).toContain("Tenant templates");
    expect(screen.getByTestId("tab-local").textContent).toContain("Project-local");

    const card = await screen.findByTestId("team-team-b");
    expect(card.textContent).toContain("View details");
    expect(card.textContent).toContain("Adopt");

    expect(screen.queryByText("Equipos")).toBeNull();
    expect(screen.getByTestId("tab-template").textContent).not.toContain("Plantillas");
  });

  it("el diálogo de adopción — el que escondía veinte textos — se traduce entero", async () => {
    renderIn("en", <TeamsListPage />);

    fireEvent.click(await screen.findByTestId("team-adopt-team-b"));

    const dialog = within(await screen.findByTestId("adopt-team-dialog"));
    expect(dialog.getByText("Adopt / Customize team")).toBeDefined();
    expect(dialog.getByLabelText("Team name")).toBeDefined();
    expect(dialog.getByText("Target")).toBeDefined();
    expect(dialog.getByText("Tenant catalog")).toBeDefined();
    expect(dialog.getByText("A project")).toBeDefined();
    expect(dialog.getByText("Team model (optional)")).toBeDefined();
    expect(dialog.getByRole("button", { name: "Adopt" })).toBeDefined();

    // El nombre por defecto también salía del diccionario privado del fichero.
    expect((dialog.getByTestId("adopt-team-name") as HTMLInputElement).value).toBe(
      "Equipo built-in (copy)",
    );

    expect(dialog.queryByText("Destino")).toBeNull();
    expect(dialog.queryByText("Catálogo del tenant")).toBeNull();
  });
});

describe("detalle del equipo", () => {
  it("en castellano rinde acciones, política de memoria y miembros", async () => {
    renderIn("es", <TeamDetailPage />);

    expect(await screen.findByText("Política de memoria del equipo")).toBeDefined();
    expect(screen.getByTestId("team-edit-button").textContent).toContain("Editar");
    expect(screen.getByTestId("team-delete-button").textContent).toContain("Borrar");
    expect(screen.getByTestId("add-member-button").textContent).toContain("Añadir miembro");
    expect((await screen.findByTestId("member-priority-a1")).textContent).toContain("Prioridad 10");
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <TeamDetailPage />);

    expect(await screen.findByText("Team memory policy")).toBeDefined();
    expect(screen.getByTestId("team-edit-button").textContent).toContain("Edit");
    expect(screen.getByTestId("team-delete-button").textContent).toContain("Delete");
    expect(screen.getByTestId("add-member-button").textContent).toContain("Add member");
    expect((await screen.findByTestId("member-priority-a1")).textContent).toContain("Priority 10");
    expect((await screen.findByTestId("leader-badge")).textContent).toBe("Leader");

    expect(screen.queryByText("Política de memoria del equipo")).toBeNull();
    expect(screen.queryByText("Añadir miembro")).toBeNull();
  });

  /**
   * El hueco que la cabecera de este fichero anotaba como «lo que este test NO
   * puede afirmar», cerrado el 2026-08-19: las cuatro opciones del `<Select>`
   * salían de `MEMORY_SCOPE_OPTIONS` con la etiqueta castellana cableada, así que
   * eran el único trozo de esta pantalla que el toggle no movía. Se comprueba
   * sobre el `<option>` y no sobre el texto suelto porque un `<Select>` cerrado
   * sí renderiza sus opciones en el DOM: si volviese el literal, salta aquí.
   */
  it("las opciones de política de memoria también se traducen (venían de un módulo compartido)", async () => {
    renderIn("en", <TeamDetailPage />);

    const select = await screen.findByTestId("team-memory-scope");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);

    expect(options).toContain("Shared with team");
    expect(options).toContain("Shared with project");
    expect(options).not.toContain("Compartida con equipo");
    expect(options).not.toContain("Privada");
  });

  it("el diálogo de alta de miembro se traduce, incluidos los dos modos", async () => {
    renderIn("en", <TeamDetailPage />);

    fireEvent.click(await screen.findByTestId("add-member-button"));

    const dialog = within(await screen.findByTestId("add-member-dialog"));
    expect(dialog.getByText("Add a member to the team")).toBeDefined();
    expect(dialog.getByLabelText("Agent")).toBeDefined();
    expect(dialog.getByText("Mode")).toBeDefined();
    expect(dialog.getByRole("button", { name: "Add" })).toBeDefined();
    expect(dialog.getByTestId("add-member-cancel").textContent).toBe("Cancel");

    expect(dialog.queryByText("Modo")).toBeNull();
    expect(dialog.queryByLabelText("Agente")).toBeNull();
  });

  it("el diálogo de borrado — el que pide teclear el nombre — se traduce", async () => {
    renderIn("en", <TeamDetailPage />);

    fireEvent.click(await screen.findByTestId("team-delete-button"));

    expect(await screen.findByText("Delete team")).toBeDefined();
    expect(screen.getByText("To confirm, type the team name:")).toBeDefined();
    expect(screen.getByTestId("delete-team-confirm").textContent).toContain("Delete permanently");
    expect(screen.queryByText("Borrar definitivamente")).toBeNull();
  });

  it("el diálogo de edición del equipo se traduce", async () => {
    renderIn("en", <TeamDetailPage />);

    fireEvent.click(await screen.findByTestId("team-edit-button"));

    expect(await screen.findByText("Edit team")).toBeDefined();
    expect(screen.getByLabelText("Name")).toBeDefined();
    expect(screen.getByTestId("edit-team-save").textContent).toBe("Save");
    expect(screen.queryByText("Editar equipo")).toBeNull();
  });

  it("el diálogo de edición de miembro se traduce", async () => {
    renderIn("en", <TeamDetailPage />);

    fireEvent.click(await screen.findByTestId("member-edit-a1"));

    const dialog = within(await screen.findByTestId("member-edit-dialog"));
    expect(dialog.getByText("Edit member")).toBeDefined();
    expect(dialog.getByText("Team leader")).toBeDefined();
    expect(dialog.getByLabelText("Role in the team")).toBeDefined();
    expect(dialog.getByLabelText("Assignment priority (0–1000)")).toBeDefined();

    expect(dialog.queryByText("Líder del equipo")).toBeNull();
  });
});
