// @vitest-environment jsdom
// T11 (c8, ciclo-vida) + C2 (runs-visor), sobre el board real con API/WS
// mockeadas:
//   - T11: la fila superior del board gerencial pinta PLANES reales (GET
//     /plans), no proyectos (ADR 0008 satisfecho).
//   - C2: click limpio en una tarjeta abre el panel de detalle; un drag NO lo
//     abre (distinción click-vs-drag, no rompe el drag&drop).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/ws", () => ({
  useWebSocket: () => {},
  wsUrl: (p: string) => `ws://test${p}`,
}));

// El sheet de detalle tiene su propia carga (runs + comentarios); aquí solo
// importa si el board lo ABRE o no (C2) — se sustituye por un marcador.
vi.mock("@/components/tasks/task-detail-sheet", () => ({
  TaskDetailSheet: ({ open }: { open: boolean }) =>
    open ? <div data-testid="task-detail-open" /> : null,
}));

import BoardPage from "@/app/admin/board/page";

const PLAN = {
  id: "plan-1",
  title: "Plan CI4",
  status: "in_progress",
  project_id: "proj-1",
};
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

function wireApi() {
  // PROY2-08: el board pagina (limit/offset); el mock matchea por prefijo.
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/plans?")) return Promise.resolve([PLAN]);
    if (path.startsWith("/projects?")) return Promise.resolve([PROJECT]);
    if (path.includes("/tasks")) return Promise.resolve([TASK]);
    return Promise.resolve([]);
  });
}

/**
 * Igual que `wireApi` pero con un fetcher de TAREAS realmente PAGINADO y un
 * estado de plan configurable. El backend aplica `DEFAULT_PAGE_SIZE=100` aunque
 * el cliente no mande `limit`, así que un mock que devuelve el array entero de
 * una vez no puede distinguir "el board pagina" de "el board truncaba en
 * silencio" — que es justo el defecto que PROY2-08 vino a arreglar.
 */
function wireApiPaged({ taskCount = 120, planStatus = PLAN.status } = {}) {
  const allTasks = Array.from({ length: taskCount }, (_, i) => ({
    ...TASK,
    id: `task-${i + 1}`,
    title: `Tarea ${i + 1}`,
    // Reparto entre dos columnas para que el conteo no dependa de una sola.
    status: i % 2 === 0 ? "ready" : "in_progress",
  }));
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/plans?")) return Promise.resolve([{ ...PLAN, status: planStatus }]);
    if (path.startsWith("/projects?")) return Promise.resolve([PROJECT]);
    if (path.includes("/tasks")) {
      const url = new URL(`http://x${path}`);
      const limit = Number(url.searchParams.get("limit") ?? "100");
      const offset = Number(url.searchParams.get("offset") ?? "0");
      return Promise.resolve(allTasks.slice(offset, offset + limit));
    }
    return Promise.resolve([]);
  });
  return allTasks;
}

/** Rutas de tareas pedidas (para comprobar que se agotaron las páginas). */
function taskCalls(): string[] {
  return apiFetchMock.mock.calls
    .map(([p]) => p as string)
    .filter((p) => typeof p === "string" && p.includes("/tasks"));
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BoardPage />
    </QueryClientProvider>,
  );
}

// Los `waitFor` de este fichero esperan transiciones de TanStack Query. El
// timeout por defecto de RTL (1s) se queda corto cuando la suite corre entera en
// paralelo y la máquina va cargada: se vio un rojo fantasma así. Se sube aquí
// (por fichero) en vez de tocar la config compartida.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Board gerencial (T11 + C2)", () => {
  it("renders PLANS (not projects) as the top-row cards", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId(`plan-card-${PLAN.id}`)).toBeTruthy());
    const card = screen.getByTestId(`plan-card-${PLAN.id}`);
    expect(card.textContent).toContain("Plan CI4");
    // El nombre del proyecto es una etiqueta DE la tarjeta del plan, no la tarjeta.
    expect(card.textContent).toContain("Proyecto Demo");
    expect(apiFetchMock).toHaveBeenCalledWith("/plans?limit=100&offset=0");
  });

  it("a clean click on a task card opens the detail panel (C2)", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId(`task-card-${TASK.id}`)).toBeTruthy());
    fireEvent.click(screen.getByTestId(`task-card-${TASK.id}`));
    expect(screen.getByTestId("task-detail-open")).toBeTruthy();
  });

  it("a drag does NOT open the detail panel (C2)", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId(`task-card-${TASK.id}`)).toBeTruthy());
    const card = screen.getByTestId(`task-card-${TASK.id}`);
    fireEvent.dragStart(card, { dataTransfer: { setData: () => {}, effectAllowed: "" } });
    // El click que el navegador dispara al soltar tras un drag no debe abrir.
    fireEvent.click(card);
    expect(screen.queryByTestId("task-detail-open")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// human_proy_03.a (remediacion-proyecto-integral-2026-07-17): «un plan de más de
// 100 tareas se ve COMPLETO en el board».
//
// El mecanismo (`fetchAllPages`) ya tenía sus tests puros en `lib/paginate.test.ts`,
// pero el board no: su test mockeaba `/tasks` devolviendo UNA tarea, así que
// habría pasado igual con el bug (`apiFetch` sin limit/offset → 100 filas y el
// resto desaparecido en silencio). Aquí se cierra el último tramo: el RENDER.
// ---------------------------------------------------------------------------
describe("Board — un plan de >100 tareas se ve completo (human_proy_03.a)", () => {
  it("pinta las 120 tarjetas, no las 100 de la primera página", async () => {
    wireApiPaged({ taskCount: 120 });
    mount();
    await waitFor(() => expect(screen.getByTestId("task-card-task-1")).toBeTruthy());
    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid^="task-card-"]')).toHaveLength(120),
    );
    // La 120ª existe por id (no es que se hayan pintado 120 copias de la 1ª).
    expect(screen.getByTestId("task-card-task-120")).toBeTruthy();
    // Y el contador de la cabecera dice 120 tareas.
    expect(screen.getByTestId("tasks-board").textContent).toContain("120 tareas");
  });

  it("agota las páginas: pide offset=0 y offset=100 del plan seleccionado", async () => {
    wireApiPaged({ taskCount: 120 });
    mount();
    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid^="task-card-"]')).toHaveLength(120),
    );
    const calls = taskCalls();
    expect(calls).toContain("/projects/proj-1/tasks?plan_id=plan-1&limit=100&offset=0");
    expect(calls).toContain("/projects/proj-1/tasks?plan_id=plan-1&limit=100&offset=100");
    // La 3ª página no se pide: la 2ª vino incompleta (20 < 100), ahí se para.
    expect(calls.some((p) => p.includes("offset=200"))).toBe(false);
  });

  it("reparte las tarjetas en sus columnas y los contadores cuadran", async () => {
    wireApiPaged({ taskCount: 120 });
    mount();
    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid^="task-card-"]')).toHaveLength(120),
    );
    // 60/60 por el reparto del fixture: si la paginación se quedara en la
    // primera página serían 50/50.
    expect(screen.getByTestId("col-count-ready").textContent).toBe("60");
    expect(screen.getByTestId("col-count-in_progress").textContent).toBe("60");
  });

  it("no avisa de truncado cuando NO se tocó el tope de seguridad", async () => {
    // El aviso es para 2000+ filas; con 120 sería un falso positivo que empuja al
    // operador a filtrar sin motivo.
    wireApiPaged({ taskCount: 120 });
    mount();
    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid^="task-card-"]')).toHaveLength(120),
    );
    expect(screen.queryByTestId("board-truncated-warning")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// hallazgo #3 (QA 2026-07-07): un plan BLOQUEADO ofrece «Desbloquear» en su
// propia tarjeta del board, no solo en /plans/{id}/escalated.
//
// `board/page.test.tsx` no mencionaba el testid, así que el botón podía
// desaparecer en cualquier refactor de la tarjeta sin que nada avisara.
// ---------------------------------------------------------------------------
describe("Board — desbloquear un plan desde su tarjeta (hallazgo #3)", () => {
  it("un plan blocked ofrece el botón y el click hace POST /plans/{id}/unblock", async () => {
    wireApiPaged({ taskCount: 2, planStatus: "blocked" });
    mount();
    await waitFor(() => expect(screen.getByTestId(`plan-unblock-${PLAN.id}`)).toBeTruthy());

    fireEvent.click(screen.getByTestId(`plan-unblock-${PLAN.id}`));

    await waitFor(() => {
      const call = apiFetchMock.mock.calls.find(([p]) => p === `/plans/${PLAN.id}/unblock`);
      expect(call).toBeDefined();
      expect((call?.[1] as { method?: string })?.method).toBe("POST");
    });
  });

  it("desbloquear NO cambia de plan seleccionado (stopPropagation)", async () => {
    // El botón vive DENTRO de una Card cuyo onClick selecciona el plan. Sin
    // `stopPropagation`, desbloquear un plan cambiaría además el tablero de
    // tareas de debajo: dos efectos por un clic.
    const BLOCKED = {
      id: "plan-2",
      title: "Plan bloqueado",
      status: "blocked",
      project_id: "proj-1",
    };
    apiFetchMock.mockImplementation((path: string) => {
      if (path.startsWith("/plans?")) return Promise.resolve([PLAN, BLOCKED]);
      if (path.startsWith("/projects?")) return Promise.resolve([PROJECT]);
      if (path.includes("/tasks")) return Promise.resolve([TASK]);
      return Promise.resolve([]);
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("plan-unblock-plan-2")).toBeTruthy());
    // Arranca con el primero seleccionado.
    expect(screen.getByTestId("plan-card-plan-1").dataset.active).toBe("true");
    expect(screen.getByTestId("plan-card-plan-2").dataset.active).toBe("false");

    fireEvent.click(screen.getByTestId("plan-unblock-plan-2"));

    await waitFor(() =>
      expect(apiFetchMock.mock.calls.some(([p]) => p === "/plans/plan-2/unblock")).toBe(true),
    );
    // La selección NO se movió.
    expect(screen.getByTestId("plan-card-plan-1").dataset.active).toBe("true");
    expect(screen.getByTestId("plan-card-plan-2").dataset.active).toBe("false");
  });

  it("un plan que NO está bloqueado no ofrece el botón", async () => {
    wireApiPaged({ taskCount: 2, planStatus: "in_progress" });
    mount();
    await waitFor(() => expect(screen.getByTestId(`plan-card-${PLAN.id}`)).toBeTruthy());
    expect(screen.queryByTestId(`plan-unblock-${PLAN.id}`)).toBeNull();
  });
});
