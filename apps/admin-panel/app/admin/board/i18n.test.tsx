// @vitest-environment jsdom

/**
 * `app/admin/board` — el doble Kanban gerencial, en los dos idiomas (plan
 * prod-16, `task_prod16_03`).
 *
 * La `ATTR_ALLOWLIST` le veía **4 atributos** en 643 líneas, y ninguno de los
 * cuatro era lo que se lee: la cabecera, las dos secciones («Planes» /
 * «Tareas»), las OCHO columnas del tablero, los cuatro estados vacíos, el aviso
 * de truncado, el botón «Desbloquear» y los dos mensajes de por qué se revierte
 * un arrastre estaban todos en castellano fijo. Es el mismo aviso que el plan
 * repite desde el 08-01: **el contador mide su patrón, no la deuda**.
 *
 * Dos decisiones que este test fija a propósito:
 *
 *   1. Las ocho columnas salen del catálogo COMPARTIDO `taskStatus`, el mismo
 *      que ya usa `projects/[id]/tasks`. El board tenía su tercera copia de la
 *      lista con el texto dentro; con el texto en el catálogo, traducir una
 *      pantalla ya no deja la otra a medias.
 *   2. El estado del PLAN, que hasta hoy se pintaba crudo (`in_progress`), sale
 *      del catálogo `planStatus` que el otro carril introdujo el mismo día. No
 *      es sólo traducción: el enum del backend no era texto de UI en ningún
 *      idioma.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/ws", () => ({
  useWebSocket: () => {},
  wsUrl: (p: string) => `ws://test${p}`,
}));

/**
 * El aviso de truncado sólo aparece cuando `fetchAllPages` agota sus 20 páginas
 * de 100, o sea con 2.000 filas: rendirlas de verdad en jsdom para leer una
 * frase es pagar segundos por nada. Se envuelve la función REAL y sólo se fuerza
 * su bandera, así que los demás casos siguen paginando como en producción.
 */
let forceTruncated = false;
vi.mock("@/lib/paginate", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/paginate")>();
  return {
    ...actual,
    fetchAllPages: async (path: string, options?: Parameters<typeof actual.fetchAllPages>[1]) => {
      const result = await actual.fetchAllPages(path, options);
      return forceTruncated ? { ...result, truncated: true } : result;
    },
  };
});

// La ficha de la tarea ya está migrada y tiene su propio test en los dos
// idiomas (`projects/[id]/tasks/i18n.test.tsx`): aquí sólo estorbaría con sus
// consultas.
vi.mock("@/components/tasks/task-detail-sheet", () => ({
  TaskDetailSheet: ({ open }: { open: boolean }) =>
    open ? <div data-testid="task-detail-open" /> : null,
}));

import BoardPage from "@/app/admin/board/page";

const STORAGE_KEY = "admin-panel.lang";

const PLAN = { id: "plan-1", title: "Plan CI4", status: "in_progress", project_id: "proj-1" };
const PROJECT = { id: "proj-1", name: "Proyecto Demo" };
const TASK = {
  id: "task-1",
  project_id: "proj-1",
  plan_id: "plan-1",
  title: "Implementar contrato",
  status: "ready",
  priority: "medium",
  description: null,
  depends_on: [],
};

function wire({ plans = [PLAN], tasks = [TASK] }: { plans?: unknown[]; tasks?: unknown[] } = {}) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/plans?")) return Promise.resolve(plans);
    if (path.startsWith("/projects?")) return Promise.resolve([PROJECT]);
    if (path.includes("/tasks")) return Promise.resolve(tasks);
    return Promise.resolve([]);
  });
}

function mount(lang: "es" | "en") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <BoardPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
  forceTruncated = false;
});

describe("tablero en los dos idiomas", () => {
  it("rinde cabecera, secciones y columnas en castellano", async () => {
    wire();
    mount("es");

    expect(await screen.findByText("Tablero")).toBeDefined();
    expect(screen.getByText("Planes")).toBeDefined();
    expect((await screen.findByTestId("col-empty-done")).textContent).toBe("Sin tareas");
    const columns = within(screen.getByTestId("board-columns"));
    expect(columns.getByText("En curso")).toBeDefined();
    expect(columns.getByText("Bloqueada")).toBeDefined();
    expect(screen.getByTestId("board-live-indicator").textContent).toBe("Tiempo real");
  });

  it("traduce cabecera, descripción y las dos secciones", async () => {
    wire();
    mount("en");

    expect(await screen.findByText("Board")).toBeDefined();
    expect(
      screen.getByText(/Plans \(management\) on top, tasks \(operational\) below/),
    ).toBeDefined();
    expect(screen.getByText("Plans")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("board-columns")).toBeTruthy());
    expect(screen.getByTestId("board-live-indicator").textContent).toBe("Live");

    expect(screen.queryByText("Tablero")).toBeNull();
    expect(screen.queryByText("Tiempo real")).toBeNull();
  });

  it("traduce las ocho columnas desde el catálogo compartido `taskStatus`", async () => {
    wire();
    mount("en");

    expect((await screen.findByTestId("col-empty-done")).textContent).toBe("No tasks");
    const columns = within(screen.getByTestId("board-columns"));
    expect(columns.getByText("In progress")).toBeDefined();
    expect(columns.getByText("Pending approval")).toBeDefined();
    expect(columns.getByText("Blocked")).toBeDefined();
    expect(columns.getByText("Cancelled")).toBeDefined();
    expect(columns.queryByText("En curso")).toBeNull();
    expect(columns.queryByText("Bloqueada")).toBeNull();
  });

  it("traduce el estado del plan, que se pintaba crudo del backend", async () => {
    wire();
    mount("en");

    const card = await screen.findByTestId("plan-card-plan-1");
    expect(within(card).getByText("In progress")).toBeDefined();
    // El enum del backend no era texto de UI en ningún idioma.
    expect(within(card).queryByText("in_progress")).toBeNull();
  });

  it("traduce los dos estados vacíos y el botón de desbloquear un plan", async () => {
    wire({ plans: [{ ...PLAN, status: "blocked" }] });
    mount("en");

    const card = await screen.findByTestId("plan-card-plan-1");
    expect(within(card).getByTestId("plan-unblock-plan-1").textContent).toContain("Unblock");
    expect(within(card).getByText("Blocked")).toBeDefined();

    cleanup();
    wire({ plans: [] });
    mount("en");
    const empty = await screen.findByTestId("plans-empty");
    expect(empty.textContent).toContain("This tenant has no plans yet");
    expect(empty.textContent).not.toContain("aún no tiene planes");
    expect(screen.getByTestId("board-no-selection").textContent).toBe(
      "Select a plan to see its tasks.",
    );
  });

  it("traduce el motivo de que un arrastre se revierta por dependencias", async () => {
    wire({
      tasks: [
        { ...TASK, id: "task-1", status: "backlog", depends_on: ["task-2"] },
        { ...TASK, id: "task-2", status: "ready", depends_on: [] },
      ],
    });
    mount("en");

    // El candado y su `title` viven en la tarjeta y no los veía la guarda.
    const lock = await screen.findByTestId("task-lock-task-1");
    expect(lock.getAttribute("title")).toContain("Blocked by 1 unfinished dependency");

    // Arrastrar a `ready` con una dependencia pendiente: el board lo rechaza
    // ANTES de llamar al backend, así que el texto es suyo.
    const column = screen.getByTestId("col-ready");
    fireEvent.drop(column, {
      dataTransfer: { getData: () => "task-1", dropEffect: "move" },
    });
    const error = await screen.findByTestId("board-drag-error");
    expect(error.textContent).toContain("Cannot move");
    expect(error.textContent).toContain("Ready");
    expect(error.textContent).not.toContain("No se puede mover");
  });

  it("traduce el aviso de tablero truncado", async () => {
    forceTruncated = true;
    wire();
    mount("en");

    const warning = await screen.findByTestId("board-truncated-warning");
    expect(warning.textContent).toContain("shows at most");
    expect(warning.textContent).not.toContain("muestra un máximo");
  });
});
