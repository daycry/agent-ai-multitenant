// @vitest-environment jsdom

/**
 * El formulario de edición de tarea (ADR 0162, ola de frontend).
 *
 * Antes de esto la API aceptaba **doce** campos actualizables y el navegador
 * editaba dos: el estado (arrastrando en el tablero) y los criterios de
 * aceptación (como texto). Título, descripción, prioridad, plan, complejidad,
 * reintentos y los dos agentes sólo se podían fijar AL CREAR la tarea; después
 * eran inmutables desde el panel, aunque el `PUT` los aceptase.
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

import { ApiError } from "@/lib/api";
import { dictionary } from "@/lib/i18n";
import { LANGS } from "@/lib/i18n";
import { TaskEditDialog } from "@/components/tasks/task-edit-dialog";

const TASK_DETAIL = {
  id: "task-1",
  tenant_id: "ten-1",
  project_id: "proj-1",
  plan_id: "plan-1",
  title: "Migrar el esquema",
  description: "Con Alembic",
  status: "backlog",
  priority: "medium",
  assigned_agent_id: "ag-1",
  reviewer_agent_id: null,
  acceptance_criteria: [],
  inputs: {},
  estimated_complexity: "m",
  retry_count: 0,
  max_retries: 3,
  depends_on: [],
};

const PLANS = [
  { id: "plan-1", title: "Plan de arranque", status: "in_progress" },
  { id: "plan-2", title: "Plan de cierre", status: "approved" },
];

const AGENTS = [
  {
    id: "ag-1",
    name: "Backend Dev",
    role: "backend_dev",
    agent_type: "ai",
    scope: "global_builtin",
    project_id: null,
    review_capability: false,
  },
  {
    id: "ag-2",
    name: "Revisor",
    role: "reviewer",
    agent_type: "ai",
    scope: "global_builtin",
    project_id: null,
    review_capability: true,
  },
  {
    id: "ag-9",
    name: "QA de otro proyecto",
    role: "qa",
    agent_type: "ai",
    scope: "project_local",
    project_id: "proj-9",
    review_capability: false,
  },
];

/** Todas las llamadas de escritura que ha visto el mock. */
function writes(): Array<[string, { method?: string; body?: unknown }]> {
  return apiFetchMock.mock.calls.filter(
    (call) => (call[1] as { method?: string } | undefined)?.method !== undefined,
  ) as Array<[string, { method?: string; body?: unknown }]>;
}

function wire(overrides: { detail?: unknown; put?: () => Promise<unknown> } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string }) => {
    if (path === "/projects/proj-1/tasks/task-1" && opts?.method === "PUT") {
      return overrides.put ? overrides.put() : Promise.resolve(TASK_DETAIL);
    }
    if (path === "/projects/proj-1/tasks/task-1") {
      return Promise.resolve(overrides.detail ?? TASK_DETAIL);
    }
    if (path === "/projects/proj-1/plans") return Promise.resolve(PLANS);
    if (path.startsWith("/agents")) return Promise.resolve(AGENTS);
    return Promise.resolve([]);
  });
}

function renderDialog(onSaved = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TaskEditDialog
        task={{ id: "task-1", project_id: "proj-1" }}
        open
        onOpenChange={vi.fn()}
        onSaved={onSaved}
      />
    </QueryClientProvider>,
  );
  return onSaved;
}

/** Espera a que el formulario esté montado Y con sus dos catálogos dentro.
 *
 * Los planes y los agentes llegan por su cuenta, después del detalle: sin
 * esperarlos, un `select` cuyo valor aún no tiene `option` devuelve "" y el
 * assert estaría midiendo la espera en vez del comportamiento. */
async function openLoaded(onSaved = vi.fn()) {
  wire();
  const spy = renderDialog(onSaved);
  await screen.findByTestId("task-edit-title");
  await screen.findByText("Plan de arranque");
  // `findAll`: cada agente aparece una vez en CADA uno de los dos desplegables.
  await screen.findAllByText("Backend Dev");
  return spy;
}

const input = (testid: string) => screen.getByTestId(testid) as HTMLInputElement;
const select = (testid: string) => screen.getByTestId(testid) as HTMLSelectElement;

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("TaskEditDialog — el formulario que no existía", () => {
  it("pinta los ocho campos editables con el valor que la tarea tiene hoy", async () => {
    await openLoaded();

    expect(input("task-edit-title").value).toBe("Migrar el esquema");
    expect((screen.getByTestId("task-edit-description-edit") as HTMLTextAreaElement).value).toBe(
      "Con Alembic",
    );
    expect(select("task-edit-priority").value).toBe("medium");
    expect(select("task-edit-plan").value).toBe("plan-1");
    expect(select("task-edit-complexity").value).toBe("m");
    expect(input("task-edit-max-retries").value).toBe("3");
    expect(select("task-edit-assignee").value).toBe("ag-1");
    // Sin revisor: el hueco vacío es una opción real, no el primer agente.
    expect(select("task-edit-reviewer").value).toBe("");
  });

  it("ofrece los agentes del tenant y del proyecto, nunca los de OTRO proyecto", async () => {
    await openLoaded();

    const assignee = select("task-edit-assignee");
    expect(within(assignee).getByText("Backend Dev")).toBeTruthy();
    // `GET /agents` devuelve también los `project_local` de otros proyectos del
    // tenant: asignarle uno a esta tarea sería un agente que no trabaja aquí.
    expect(within(assignee).queryByText("QA de otro proyecto")).toBeNull();
  });

  it("agota las páginas del catálogo de agentes en vez de quedarse en la primera", async () => {
    // `GET /agents` trunca en silencio a 100 filas (DEFAULT_PAGE_SIZE). Un
    // tenant con más agentes vería un desplegable al que le faltan justo los
    // que no salen en la primera página.
    await openLoaded();
    await waitFor(() =>
      expect(apiFetchMock.mock.calls.some(([path]) => /^\/agents\?.*limit=/.test(path))).toBe(true),
    );
  });

  it("mientras el catálogo no llega, conserva el valor en vez de fingir «ninguno»", async () => {
    // Un `select` cuyo `value` no casa con ninguna `option` pinta la PRIMERA
    // como si estuviera elegida — aquí, «Sin plan» y «Sin fijar». El operador
    // lo lee, no toca el campo, y se va convencido de que la tarea no cuelga de
    // ningún plan ni la tiene nadie asignada.
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/projects/proj-1/tasks/task-1") return Promise.resolve(TASK_DETAIL);
      // Los dos catálogos se quedan colgados a propósito.
      if (path === "/projects/proj-1/plans") return new Promise(() => {});
      if (path.startsWith("/agents")) return new Promise(() => {});
      return Promise.resolve([]);
    });
    renderDialog();
    await screen.findByTestId("task-edit-title");

    expect(select("task-edit-plan").value).toBe("plan-1");
    expect(select("task-edit-assignee").value).toBe("ag-1");
  });

  it("manda EXACTAMENTE los campos que el operador cambió", async () => {
    const onSaved = await openLoaded();

    fireEvent.change(input("task-edit-title"), { target: { value: "  Migrar el esquema v2  " } });
    fireEvent.change(select("task-edit-priority"), { target: { value: "high" } });
    fireEvent.click(screen.getByTestId("task-edit-submit"));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(writes()).toEqual([
      [
        "/projects/proj-1/tasks/task-1",
        { method: "PUT", body: { title: "Migrar el esquema v2", priority: "high" } },
      ],
    ]);
  });

  it("desasignar un agente manda null explícito, que es lo que vacía la columna", async () => {
    await openLoaded();

    fireEvent.change(select("task-edit-assignee"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("task-edit-submit"));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0][1].body).toEqual({ assigned_agent_id: null });
  });

  it("no ofrece guardar cuando no hay ningún cambio", async () => {
    await openLoaded();

    expect((screen.getByTestId("task-edit-submit") as HTMLButtonElement).disabled).toBe(true);
    expect(writes()).toEqual([]);
  });
});

describe("TaskEditDialog — la validación de cliente espeja al servidor", () => {
  it("no deja mandar un título vacío, que el servidor devolvería como 422", async () => {
    await openLoaded();

    fireEvent.change(input("task-edit-title"), { target: { value: "   " } });

    expect(screen.getByTestId("task-edit-validation").textContent).toBeTruthy();
    expect((screen.getByTestId("task-edit-submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("task-edit-submit"));
    expect(writes()).toEqual([]);
  });

  it("no deja mandar un título más largo que el máximo del servidor", async () => {
    await openLoaded();

    fireEvent.change(input("task-edit-title"), { target: { value: "x".repeat(201) } });

    expect(screen.getByTestId("task-edit-validation").textContent).toContain("200");
    expect((screen.getByTestId("task-edit-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("no deja mandar unos reintentos fuera del rango [0, 20]", async () => {
    await openLoaded();

    fireEvent.change(input("task-edit-max-retries"), { target: { value: "21" } });

    expect(screen.getByTestId("task-edit-validation").textContent).toContain("20");
    expect((screen.getByTestId("task-edit-submit") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("task-edit-submit"));
    expect(writes()).toEqual([]);
  });

  it("no deja mandar unos reintentos vacíos", async () => {
    await openLoaded();

    fireEvent.change(input("task-edit-max-retries"), { target: { value: "" } });

    expect(screen.getByTestId("task-edit-validation").textContent).toBeTruthy();
    expect((screen.getByTestId("task-edit-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("avisa —sin bloquear— de que revisor e implementador serían el mismo agente", async () => {
    // El `PUT` lo acepta, así que no se bloquea; pero el materializador de
    // planes se niega a emparejarlos a propósito (`_resolve_assignment`),
    // porque un agente que se revisa a sí mismo no revisa nada.
    await openLoaded();

    fireEvent.change(select("task-edit-reviewer"), { target: { value: "ag-1" } });

    expect(screen.getByTestId("task-edit-warning").textContent).toBeTruthy();
    expect((screen.getByTestId("task-edit-submit") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("TaskEditDialog — estados", () => {
  it("dice que está cargando antes de tener la tarea", () => {
    apiFetchMock.mockImplementation(() => new Promise(() => {}));
    renderDialog();

    expect(screen.getByTestId("task-edit-loading")).toBeTruthy();
    expect(screen.queryByTestId("task-edit-title")).toBeNull();
  });

  it("enseña el error de carga en vez de un formulario vacío", async () => {
    apiFetchMock.mockRejectedValue(new ApiError(404, '{"detail":"task not found"}'));
    renderDialog();

    const error = await screen.findByTestId("task-edit-load-error");
    expect(error.textContent).toContain("task not found");
    expect(screen.queryByTestId("task-edit-title")).toBeNull();
  });

  it("enseña el rechazo del servidor al usuario en vez de tragárselo", async () => {
    wire({
      put: () =>
        Promise.reject(
          new ApiError(422, '{"detail":[{"loc":["body","title"],"msg":"too long","type":"x"}]}'),
        ),
    });
    const onSaved = vi.fn();
    renderDialog(onSaved);
    await screen.findByTestId("task-edit-title");

    fireEvent.change(input("task-edit-title"), { target: { value: "Otro título" } });
    fireEvent.click(screen.getByTestId("task-edit-submit"));

    const error = await screen.findByTestId("task-edit-error");
    expect(error.textContent).toContain("too long");
    expect(onSaved).not.toHaveBeenCalled();
  });
});

describe("TaskEditDialog — i18n", () => {
  it("todas sus claves están en los dos idiomas", () => {
    const entries = Object.entries(dictionary.taskEdit);
    // Sin este suelo, el día que el namespace se renombre el test pasaría
    // en vacío afirmando que cero claves están bien traducidas.
    expect(entries.length).toBeGreaterThanOrEqual(20);
    for (const [key, texts] of entries) {
      for (const lang of LANGS) {
        expect(`${key}.${lang}: ${texts[lang]}`).toMatch(/: \S/);
      }
    }
  });
});
