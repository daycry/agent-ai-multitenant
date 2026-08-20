// @vitest-environment jsdom

/**
 * `projects/[id]/tasks` y la ficha COMPARTIDA de `components/tasks/*`, migrados
 * al diccionario (plan prod-16, `task_prod16_03`).
 *
 * Esta casilla venía con una advertencia escrita en el plan, y era exacta: el
 * texto de esta pantalla **no es de esta pantalla**. Está repartido entre
 * `tasks/page.tsx` y tres ficheros de `components/tasks/` que montan además
 * `app/admin/board` (el tablero por plan) y `app/admin/plans/[id]/escalated`
 * (el panel de tareas escaladas). La `ATTR_ALLOWLIST` marcaba 4 atributos en el
 * `page.tsx` y 10 más en los componentes… repartidos como deuda de tres
 * pantallas que ninguna de las tres «tenía».
 *
 * Por eso entra la ficha COMPLETA —criterios de aceptación con su editor y su
 * diálogo de comparación, el veredicto del reviewer, los runs, los comentarios
 * y las cinco acciones humanas con sus dos diálogos—: migrar sólo el `page.tsx`
 * habría dejado el Kanban del proyecto abriendo una ficha en castellano, que es
 * literalmente el fallo que este plan cierra.
 *
 * Lo que NO entra, y consta: el texto PROPIO de `board/page.tsx` y de
 * `escalated/page.tsx` (sus cabeceras, sus columnas, sus estados vacíos). Son
 * de otro lote; lo que este lote les deja es la ficha ya bilingüe.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/tasks",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// `RoleGuard` consulta el usuario actual; las acciones humanas se prueban sobre
// el componente directamente, así que aquí basta con dejarlo pasar.
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import ProjectTasksPage from "@/app/admin/projects/[id]/tasks/page";
import { TaskDetailSheet } from "@/components/tasks/task-detail-sheet";
import { TaskHumanActions } from "@/components/tasks/task-human-actions";

const STORAGE_KEY = "admin-panel.lang";

const TASK = {
  id: "task-1",
  project_id: "proj-1",
  plan_id: null,
  title: "Migrar el esquema",
  description: null,
  status: "backlog",
  priority: "medium",
  assigned_agent_id: null,
};

const TASK_DETAIL = {
  ...TASK,
  acceptance_criteria: ["El endpoint devuelve 200"],
  depends_on: ["task-2"],
  inputs: {},
};

function wire(tasks: unknown[] = [TASK], detail: unknown = TASK_DETAIL) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string }) => {
    if (path === "/projects/proj-1/tasks" && opts?.method === "POST") {
      return Promise.resolve(TASK);
    }
    if (path === "/projects/proj-1/tasks") return Promise.resolve(tasks);
    if (path === "/projects/proj-1/plans") return Promise.resolve([]);
    if (path === "/projects/proj-1/tasks/task-1") return Promise.resolve(detail);
    if (path.startsWith("/tasks/task-1/history")) {
      return Promise.resolve({
        events: [
          {
            id: "e1",
            at: 1,
            kind: "review_comment",
            actor: "reviewer",
            payload: {
              criteria: [
                { text: "El endpoint devuelve 200", passed: true },
                { text: "Los tests pasan", passed: false, evidence: "3 fallos" },
              ],
            },
          },
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

const page = (lang: "es" | "en", tasks?: unknown[]) => {
  wire(tasks);
  return renderIn(lang, <ProjectTasksPage />);
};

const sheet = (lang: "es" | "en", detail: unknown = TASK_DETAIL) => {
  wire([TASK], detail);
  return renderIn(
    lang,
    <TaskDetailSheet
      task={{ id: "task-1", project_id: "proj-1", title: "Migrar el esquema" }}
      open
      onOpenChange={() => {}}
    />,
  );
};

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("tasks del proyecto en los dos idiomas", () => {
  it("rinde cabecera, filtros y fila en castellano", async () => {
    page("es");

    expect(await screen.findByText("Tasks del proyecto")).toBeDefined();
    expect(screen.getByTestId("tasks-filter-all").textContent).toContain("Todas");
    expect(screen.getByTestId("tasks-filter-null").textContent).toContain("Sin plan");
    await waitFor(() => expect(screen.getByTestId("tasks-list")).toBeTruthy());
    expect(screen.getByTestId("task-row-task-1-status").textContent).toBe("Backlog");
    expect(screen.getByText("Sin plan asignado")).toBeDefined();
  });

  it("traduce cabecera, descripción, filtros y estado vacío", async () => {
    page("en", []);

    expect(await screen.findByText("Project tasks")).toBeDefined();
    expect(screen.getByText(/All tasks — including those not attached to a plan/)).toBeDefined();
    expect(screen.getByRole("button", { name: /Create task/ })).toBeDefined();
    expect(screen.getByTestId("tasks-plan-filter").getAttribute("aria-label")).toBe(
      "Filter tasks by plan",
    );
    expect(screen.getByTestId("tasks-filter-all").textContent).toContain("All");
    expect(screen.getByTestId("tasks-filter-null").textContent).toContain("No plan");
    const empty = await screen.findByTestId("tasks-empty");
    expect(empty.textContent).toBe("This project has no tasks yet.");

    expect(screen.queryByText("Tasks del proyecto")).toBeNull();
    expect(screen.queryByText(/Este proyecto no tiene tareas/)).toBeNull();
  });

  it("traduce los estados de las columnas del Kanban", async () => {
    page("en");

    await waitFor(() => expect(screen.getByTestId("tasks-list")).toBeTruthy());
    expect(screen.getByTestId("task-row-task-1-status").textContent).toBe("Backlog");
    expect(screen.getByText("No plan assigned")).toBeDefined();

    /*
     * El conmutador lista/Kanban es el sexto ejemplo del aviso del plan: sus
     * tres textos estaban en castellano fijo y la guarda le veía CERO
     * («Cambiar vista» y «Lista» no llevan tilde ni están en su lista de
     * palabras). Se afirma aquí porque, comprobado por mutación, devolverlo a
     * `label="Lista"` NO rompe `check-i18n` — sólo lo caza este assert.
     */
    expect(screen.getByTestId("view-toggle").getAttribute("aria-label")).toBe("Change view");
    expect(screen.getByTestId("view-toggle-list").textContent).toContain("List");
    expect(screen.getByTestId("view-toggle-list").textContent).not.toContain("Lista");

    // La vista Kanban: las ocho columnas salen del catálogo compartido.
    fireEvent.click(screen.getByTestId("view-toggle-kanban"));
    await waitFor(() => expect(screen.getByTestId("tasks-kanban-columns")).toBeTruthy());
    const columns = screen.getByTestId("tasks-kanban-columns");
    expect(within(columns).getByText("In progress")).toBeDefined();
    expect(within(columns).getByText("Pending approval")).toBeDefined();
    expect(within(columns).getByText("Blocked")).toBeDefined();
    expect(within(columns).queryByText("En curso")).toBeNull();
    expect(within(columns).queryByText("Bloqueada")).toBeNull();
    expect(screen.getByTestId("tasks-col-empty-ready").textContent).toBe("No tasks");
  });

  it("traduce el diálogo de alta de tarea, incluido el selector de prioridad", async () => {
    page("en");

    fireEvent.click(await screen.findByTestId("tasks-create-button"));
    await waitFor(() => expect(screen.getByTestId("create-task-title")).toBeTruthy());

    expect(screen.getByText(/Tasks can hang off an existing plan/)).toBeDefined();
    expect(screen.getByText("Title")).toBeDefined();
    expect(screen.getByText("Priority")).toBeDefined();
    const priority = screen.getByTestId("create-task-priority");
    expect(within(priority).getByText("Medium")).toBeDefined();
    expect(within(priority).getByText("Critical")).toBeDefined();
    expect(within(priority).queryByText("Crítica")).toBeNull();
    const plan = screen.getByTestId("create-task-plan");
    expect(within(plan).getByText("No plan (free task)")).toBeDefined();
    expect(screen.getByTestId("create-task-submit").textContent).toBe("Create task");
  });
});

describe("ficha de la tarea en los dos idiomas", () => {
  it("rinde secciones y acciones en castellano", async () => {
    sheet("es");

    expect(await screen.findByText("Criterios de aceptación")).toBeDefined();
    expect(screen.getByText("Depende de")).toBeDefined();
    // La tarea del fixture no tiene plan, así que en vez del hilo de
    // comentarios sale el aviso de por qué no lo hay.
    expect(
      screen.getByText("Los comentarios están disponibles para tareas de un plan."),
    ).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("task-review-criteria")).toBeTruthy());
    expect(screen.getByText("Veredicto del reviewer")).toBeDefined();
  });

  it("traduce criterios, dependencias, runs y comentarios", async () => {
    sheet("en");

    expect(await screen.findByText("Acceptance criteria")).toBeDefined();
    expect(screen.getByTestId("task-criteria-generate").textContent).toBe("Regenerate with AI");
    expect(screen.getByTestId("task-criteria-edit").textContent).toBe("Edit");
    expect(screen.getByText("Depends on")).toBeDefined();
    expect(screen.getByTestId("task-detail-runs-empty").textContent).toBe(
      "This task has no executions yet.",
    );
    // La tarea del fixture no tiene plan: los comentarios avisan de por qué no.
    expect(
      screen.getByText(/Comments are available for tasks that belong to a plan/),
    ).toBeDefined();

    expect(screen.queryByText("Criterios de aceptación")).toBeNull();
    expect(screen.queryByText("Depende de")).toBeNull();
  });

  it("traduce el veredicto del reviewer, criterio a criterio", async () => {
    sheet("en");

    await waitFor(() => expect(screen.getByTestId("task-review-criteria")).toBeTruthy());
    const section = screen.getByTestId("task-review-criteria");
    expect(section.textContent).toContain("Reviewer verdict");
    expect(section.textContent).toContain("1 of 2 not met");
    expect(within(section).getByLabelText("met")).toBeDefined();
    expect(within(section).getByLabelText("not met")).toBeDefined();

    expect(section.textContent).not.toContain("Veredicto del reviewer");
  });

  it("traduce el editor de criterios y su diálogo de comparación", async () => {
    sheet("en");

    fireEvent.click(await screen.findByTestId("task-criteria-edit"));
    await waitFor(() => expect(screen.getByTestId("task-criterion-input")).toBeTruthy());
    expect(screen.getByTestId("task-criterion-input").getAttribute("placeholder")).toBe(
      "A concrete, verifiable condition…",
    );
    expect(screen.getByTestId("task-criterion-remove-0").getAttribute("aria-label")).toBe(
      "Remove criterion",
    );
    expect(screen.getByTestId("task-criterion-add").textContent).toContain("Add criterion");
    expect(screen.getByTestId("task-criteria-save").textContent).toBe("Save");
    expect(screen.getByTestId("task-criteria-cancel").textContent).toBe("Cancel");
  });

  it("traduce el diálogo de comparación de criterios generados por IA", async () => {
    apiFetchMock.mockImplementation((path: string, opts?: { method?: string }) => {
      if (path.endsWith("/generate-acceptance-criteria") && opts?.method === "POST") {
        return Promise.resolve({ acceptance_criteria: ["Otro criterio"] });
      }
      if (path === "/projects/proj-1/tasks/task-1") return Promise.resolve(TASK_DETAIL);
      return Promise.resolve([]);
    });
    renderIn(
      "en",
      <TaskDetailSheet
        task={{ id: "task-1", project_id: "proj-1", title: "Migrar el esquema" }}
        open
        onOpenChange={() => {}}
      />,
    );

    fireEvent.click(await screen.findByTestId("task-criteria-generate"));
    await waitFor(() => expect(screen.getByTestId("task-criteria-compare")).toBeTruthy());
    const dialog = within(screen.getByTestId("task-criteria-compare"));
    expect(dialog.getByText("Compare acceptance criteria")).toBeDefined();
    expect(dialog.getByText("Current")).toBeDefined();
    expect(dialog.getByText("Proposed")).toBeDefined();
    expect(screen.getByTestId("task-criteria-compare-accept").textContent).toBe("Accept changes");
  });
});

describe("acciones humanas sobre una tarea en los dos idiomas", () => {
  const actions = (lang: "es" | "en") =>
    renderIn(lang, <TaskHumanActions taskId="task-1" onApplied={() => {}} />);

  it("rinde los cinco botones en castellano", () => {
    actions("es");

    expect(screen.getByTestId("approve-task-1").textContent).toContain("Aprobar manualmente");
    expect(screen.getByTestId("block-task-1").textContent).toContain("Bloquear con motivo");
  });

  it("traduce los cinco botones", () => {
    actions("en");

    expect(screen.getByTestId("approve-task-1").textContent).toContain("Approve manually");
    expect(screen.getByTestId("retry-task-1").textContent).toContain("Retry");
    expect(screen.getByTestId("reassign-task-1").textContent).toContain("Reassign with guidance");
    expect(screen.getByTestId("block-task-1").textContent).toContain("Block with a reason");
    expect(screen.getByTestId("cancel-task-1").textContent).toContain("Cancel");

    expect(screen.queryByText("Aprobar manualmente")).toBeNull();
  });

  it("traduce el diálogo de reasignar con guía", async () => {
    actions("en");

    fireEvent.click(screen.getByTestId("reassign-task-1"));
    await waitFor(() => expect(screen.getByTestId("reassign-guidance-edit")).toBeTruthy());

    expect(screen.getByText(/Sends the task back to the backlog/)).toBeDefined();
    expect(screen.getByText("Guidance for the agent")).toBeDefined();
    expect(screen.getByTestId("reassign-guidance-edit").getAttribute("placeholder")).toContain(
      "Try another approach",
    );
    expect(screen.getByTestId("reassign-submit").textContent).toBe("Reassign");
  });

  it("traduce el diálogo de bloquear con motivo", async () => {
    actions("en");

    fireEvent.click(screen.getByTestId("block-task-1"));
    await waitFor(() => expect(screen.getByTestId("block-reason-edit")).toBeTruthy());

    expect(screen.getByText(/Marks the task as blocked by an external cause/)).toBeDefined();
    expect(screen.getByText("Reason for blocking")).toBeDefined();
    expect(screen.getByTestId("block-submit").textContent).toBe("Block");
  });
});
