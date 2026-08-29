// @vitest-environment jsdom
// `task_wf_40`: la ficha de la tarea ofrece las acciones humanas cuando —y solo
// cuando— la tarea está parada esperando a una persona que además pueda actuar.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/runs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/runs")>();
  return { ...actual, listRuns: () => Promise.resolve([]) };
});

const currentUser = vi.fn();
vi.mock("@/lib/use-current-user", () => ({ useCurrentUser: () => currentUser() }));

import { TaskDetailSheet } from "@/components/tasks/task-detail-sheet";

const ADMIN = { isSystemAdmin: false, isTenantAdmin: true, isTenantMember: true, isLoading: false };
const MEMBER = {
  isSystemAdmin: false,
  isTenantAdmin: false,
  isTenantMember: true,
  isLoading: false,
};

function detail(status: string) {
  return {
    id: "t-1",
    project_id: "p-1",
    plan_id: null,
    title: "Implementar webhook",
    description: null,
    status,
    priority: "medium",
    acceptance_criteria: [],
    depends_on: [],
    inputs: {},
    // Los tres campos que sólo trae el detalle completo: la ficha no los pinta,
    // pero el formulario de edición que cuelga de ella los siembra desde la
    // MISMA `queryKey`, y sin ellos el fixture no sería el que devuelve la API.
    estimated_complexity: "m",
    max_retries: 3,
    assigned_agent_id: null,
    reviewer_agent_id: null,
  };
}

function renderSheet(status: string, who = ADMIN) {
  currentUser.mockReturnValue(who);
  // Responde POR RUTA y no lo mismo a todo: la ficha cuelga del formulario de
  // edición, que pide además los planes del proyecto y el catálogo de agentes.
  // Con un `mockResolvedValue` único, esas dos listas recibían el objeto de la
  // tarea y el `.map` reventaba — un fallo del fixture, no del componente.
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/projects/p-1/tasks/t-1") return Promise.resolve(detail(status));
    if (path.startsWith("/tasks/t-1/history")) return Promise.resolve({ events: [] });
    return Promise.resolve([]);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TaskDetailSheet
        task={{ id: "t-1", project_id: "p-1", title: "Implementar webhook" }}
        open
        onOpenChange={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TaskDetailSheet · acciones humanas", () => {
  it("offers them on a blocked task, which is the case that had no way out", async () => {
    // Una tarea `blocked` por un run que falló de forma ordinaria NO escala, así
    // que el panel de escaladas del plan nunca la muestra: esta ficha era el
    // único sitio desde el que se podía llegar a ella, y no ofrecía nada.
    renderSheet("blocked");
    expect(await screen.findByTestId("task-human-actions-t-1")).toBeTruthy();
    expect(screen.getByTestId("retry-t-1")).toBeTruthy();
  });

  it("offers them on an escalated task too", async () => {
    renderSheet("awaiting_human_approval");
    expect(await screen.findByTestId("task-human-actions-t-1")).toBeTruthy();
  });

  it("hides them on a task the backend would reject", async () => {
    renderSheet("running");
    // Espera a que el detalle cargue: si no, «no está» sería cierto por lento.
    expect(await screen.findByText("running")).toBeTruthy();
    expect(screen.queryByTestId("task-human-actions-t-1")).toBeNull();
  });

  it("abre el formulario de edición, que es la única puerta a los ocho campos", async () => {
    // El tablero por plan monta esta misma ficha: con el botón sólo en la lista
    // del proyecto, media plataforma seguiría sin poder cambiarle el agente a
    // una tarea (ADR 0162).
    renderSheet("backlog");

    fireEvent.click(await screen.findByTestId("task-detail-edit"));

    expect(await screen.findByTestId("task-edit-dialog")).toBeTruthy();
    expect(await screen.findByTestId("task-edit-assignee")).toBeTruthy();
  });

  it("hides them from a member who is not a tenant admin", async () => {
    // El endpoint exige `require_tenant_admin`; enseñárselas a un miembro sería
    // ofrecerle cinco botones que devuelven 403.
    renderSheet("blocked", MEMBER);
    expect(await screen.findByText("blocked")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("task-human-actions-t-1")).toBeNull());
  });
});
