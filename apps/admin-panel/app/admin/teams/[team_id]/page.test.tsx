// @vitest-environment jsdom
/**
 * Red de caracterización de la ficha de equipo (prod-16 `task_prod16_08`).
 *
 * Esta pantalla tenía **cero** tests con 914 líneas dentro. Antes de trocearla
 * hay que fijar lo que hace, y en particular las cuatro cosas que un corte mal
 * dado rompe sin que se note hasta producción:
 *
 *  - **built-in = solo lectura**: sin editar, sin borrar, sin añadir miembros, y
 *    con la salida («adóptalo») visible. Si el troceo pierde el `isReadOnly` de
 *    una pieza, se abre la escritura sobre una plantilla de plataforma;
 *  - **añadir en modo forked son DOS llamadas** y la segunda usa el id del fork,
 *    no el del agente original — meter el original dejaría el equipo apuntando a
 *    la plantilla y el fork huérfano;
 *  - el badge Linked/Forked sale de `forked_from_agent_id`, **no del scope**
 *    (el scope mentía; se corrigió en `task_06_17_12`);
 *  - «Sin política» de memoria manda `null`, no `""` — el string vacío no es un
 *    scope válido y el backend lo rechazaría.
 *
 * Los tres módulos pesados que la pantalla compone (`CapabilityHub`,
 * `ChatModelSection`, `AdoptTeamDialog`) se sustituyen por dobles: aquí se
 * caracteriza la ficha, no ellos, y cada uno tiene sus propios tests.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: () => ({ team_id: "team-1" }),
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/components/capability/capability-hub", () => ({
  CapabilityHub: () => <div data-testid="capability-hub-stub" />,
}));
vi.mock("@/components/capability/chat-model-section", () => ({
  ChatModelSection: ({ idPrefix }: { idPrefix: string }) => (
    <div data-testid={`chat-model-${idPrefix}`} />
  ),
}));
vi.mock("@/components/teams/adopt-team-dialog", () => ({
  AdoptTeamDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="adopt-dialog-stub" /> : null,
}));

import TeamDetailPage from "@/app/admin/teams/[team_id]/page";

const TEAM = {
  id: "team-1",
  tenant_id: "t1",
  name: "Equipo Backend",
  description: "El equipo de backend",
  is_builtin: false,
  forked_from_team_id: null as string | null,
  model_config: {},
  chat_model_config: {},
  memory_scope: "team_shared" as string | null,
  members: [
    { agent_id: "a1", role_in_team: "Tech Lead", is_team_leader: true, assignment_priority: 10 },
    { agent_id: "a2", role_in_team: null, is_team_leader: false, assignment_priority: 0 },
  ],
};

const AGENTS = [
  {
    id: "a1",
    name: "Ada",
    role: "backend_dev",
    scope: "tenant",
    project_id: null,
    forked_from_agent_id: null,
  },
  {
    id: "a2",
    name: "Linus",
    role: "qa",
    scope: "project_local",
    project_id: "p1",
    forked_from_agent_id: "a9",
  },
  {
    id: "a3",
    name: "Grace",
    role: "reviewer",
    scope: "tenant",
    project_id: null,
    forked_from_agent_id: null,
  },
];

const PROJECTS = [
  { id: "p1", name: "Proyecto Uno", is_template: false },
  { id: "p2", name: "Plantilla", is_template: true },
];

let team = TEAM;

function routeApi(path: string): unknown {
  if (path === "/teams/team-1") return team;
  if (path === "/agents") return AGENTS;
  if (path === "/projects") return PROJECTS;
  if (path === "/agents/a3/fork") return { ...AGENTS[2], id: "a3-fork" };
  if (path === "/teams/team-1/members") return team;
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TeamDetailPage />
    </QueryClientProvider>,
  );
}

/** [ruta, init] de las llamadas de escritura (las que llevan `init`). */
function writes(): [string, { method?: string; body?: Record<string, unknown> }][] {
  return apiFetchMock.mock.calls.filter(([, init]) => init !== undefined) as [
    string,
    { method?: string; body?: Record<string, unknown> },
  ][];
}

beforeEach(() => {
  team = TEAM;
  pushMock.mockReset();
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation(async (path: string) => routeApi(path));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ficha de un equipo del tenant", () => {
  it("lista los miembros con su rol, su líder y su prioridad", async () => {
    renderPage();

    expect(await screen.findByTestId("members-list")).toBeTruthy();
    expect(screen.getByTestId("member-a1").textContent).toContain("Ada");
    expect(screen.getByTestId("member-a1").textContent).toContain("Tech Lead");
    // Sin rol propio cae al del agente, no a un hueco.
    expect(screen.getByTestId("member-a2").textContent).toContain("qa");
    expect(screen.getByTestId("leader-badge")).toBeTruthy();
    expect(screen.getByTestId("member-priority-a1").textContent).toContain("10");
  });

  it("el badge Linked/Forked sale del origen del agente, no de su scope", async () => {
    renderPage();
    await screen.findByTestId("members-list");

    // `a1` es scope tenant y sin origen ⇒ Linked. `a2` es project_local CON
    // origen ⇒ Forked. Si esto se derivara del scope, saldría al revés.
    expect(screen.getByTestId("member-linked-a1")).toBeTruthy();
    expect(screen.getByTestId("member-forked-a2")).toBeTruthy();
  });

  it("deja editar y borrar, y no ofrece adoptar", async () => {
    renderPage();

    expect(await screen.findByTestId("team-edit-button")).toBeTruthy();
    expect(screen.getByTestId("team-delete-button")).toBeTruthy();
    expect(screen.queryByTestId("team-adopt-button")).toBeNull();
    expect(screen.getByTestId("add-member-button")).toHaveProperty("disabled", false);
  });

  it("«Sin política» de memoria manda null, no cadena vacía", async () => {
    renderPage();

    const select = await screen.findByTestId("team-memory-scope");
    expect(select).toHaveProperty("value", "team_shared");
    fireEvent.change(select, { target: { value: "" } });

    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0][0]).toBe("/teams/team-1");
    expect(writes()[0][1].body).toEqual({ memory_scope: null });
  });
});

describe("equipo built-in (plantilla de la plataforma)", () => {
  beforeEach(() => {
    team = { ...TEAM, is_builtin: true };
  });

  it("es de solo lectura y ofrece adoptarlo como salida", async () => {
    renderPage();

    expect(await screen.findByTestId("team-adopt-button")).toBeTruthy();
    expect(screen.queryByTestId("team-edit-button")).toBeNull();
    expect(screen.queryByTestId("team-delete-button")).toBeNull();
    expect(screen.getByTestId("add-member-button")).toHaveProperty("disabled", true);
    expect(screen.getByTestId("team-memory-scope")).toHaveProperty("disabled", true);
    expect(screen.getByTestId("team-model-adopt-hint")).toBeTruthy();
  });

  it("no deja editar la metadata de un miembro", async () => {
    renderPage();
    await screen.findByTestId("members-list");

    expect(screen.queryByTestId("member-edit-a1")).toBeNull();
  });
});

describe("añadir miembro", () => {
  it("el selector no ofrece a los que ya están en el equipo", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("add-member-button"));

    const options = Array.from(
      (await screen.findByTestId("agent-select")).querySelectorAll("option"),
    ).map((o) => o.textContent);
    expect(options.some((o) => o?.includes("Grace"))).toBe(true);
    expect(options.some((o) => o?.includes("Ada"))).toBe(false);
    expect(options.some((o) => o?.includes("Linus"))).toBe(false);
  });

  it("en modo linked manda UNA llamada, con el agente elegido", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("add-member-button"));
    fireEvent.change(await screen.findByTestId("agent-select"), { target: { value: "a3" } });
    fireEvent.click(screen.getByTestId("add-member-submit"));

    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0][0]).toBe("/teams/team-1/members");
    expect(writes()[0][1].body).toEqual({ agent_id: "a3" });
  });

  it("en modo forked clona primero y añade el FORK, no el original", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("add-member-button"));
    fireEvent.change(await screen.findByTestId("agent-select"), { target: { value: "a3" } });
    fireEvent.click(screen.getByTestId("mode-forked"));
    fireEvent.change(await screen.findByTestId("project-select"), { target: { value: "p1" } });
    fireEvent.click(screen.getByTestId("add-member-submit"));

    await waitFor(() => expect(writes().length).toBe(2));
    expect(writes()[0][0]).toBe("/agents/a3/fork");
    expect(writes()[0][1].body).toEqual({ project_id: "p1" });
    expect(writes()[1][0]).toBe("/teams/team-1/members");
    expect(writes()[1][1].body).toEqual({ agent_id: "a3-fork" });
  });

  it("el destino del fork sólo ofrece proyectos reales, no plantillas", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("add-member-button"));
    fireEvent.click(await screen.findByTestId("mode-forked"));

    const options = Array.from(
      (await screen.findByTestId("project-select")).querySelectorAll("option"),
    ).map((o) => o.textContent);
    expect(options).toContain("Proyecto Uno");
    expect(options).not.toContain("Plantilla");
  });

  it("sin proyecto destino, forked no deja enviar", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("add-member-button"));
    fireEvent.change(await screen.findByTestId("agent-select"), { target: { value: "a3" } });
    fireEvent.click(screen.getByTestId("mode-forked"));

    expect(screen.getByTestId("add-member-submit")).toHaveProperty("disabled", true);
  });
});

describe("editar la metadata de un miembro", () => {
  it("precarga lo guardado y manda rol vacío como null", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("member-edit-a1"));

    expect(await screen.findByTestId("member-edit-leader")).toHaveProperty("checked", true);
    expect(screen.getByTestId("member-edit-role")).toHaveProperty("value", "Tech Lead");
    expect(screen.getByTestId("member-edit-priority")).toHaveProperty("value", "10");

    fireEvent.change(screen.getByTestId("member-edit-role"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("member-edit-save"));

    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0][0]).toBe("/teams/team-1/members/a1");
    expect(writes()[0][1].body).toEqual({
      is_team_leader: true,
      role_in_team: null,
      assignment_priority: 10,
    });
  });

  it("una prioridad fuera de 0–1000 no se puede guardar", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("member-edit-a1"));
    fireEvent.change(await screen.findByTestId("member-edit-priority"), {
      target: { value: "5000" },
    });

    expect(screen.getByTestId("member-edit-save")).toHaveProperty("disabled", true);
  });
});
