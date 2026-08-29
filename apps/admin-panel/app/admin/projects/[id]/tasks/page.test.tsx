// @vitest-environment jsdom

/**
 * La puerta al formulario de edición desde la LISTA de tareas del proyecto
 * (ADR 0162).
 *
 * Existe como test propio porque el diálogo y su botón se pueden entregar por
 * separado, y entonces el diálogo sería un mecanismo perfecto que nadie puede
 * abrir — el modo de fallo que `verificar-antes-de-implementar` §5 llama
 * «mecanismo entregado, cero llamantes». La vista de lista no abre la ficha de
 * la tarea (eso es de las tarjetas del Kanban), así que necesita su propia
 * entrada: sin ella, editar obligaría a cambiar de vista primero.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

import ProjectTasksPage from "@/app/admin/projects/[id]/tasks/page";

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
  reviewer_agent_id: null,
  acceptance_criteria: [],
  inputs: {},
  estimated_complexity: "m",
  retry_count: 0,
  max_retries: 3,
  depends_on: [],
};

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("lista de tareas del proyecto", () => {
  it("cada fila abre el formulario de edición de SU tarea", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/projects/proj-1/tasks/task-1") return Promise.resolve(TASK_DETAIL);
      if (path === "/projects/proj-1/tasks") return Promise.resolve([TASK]);
      return Promise.resolve([]);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ProjectTasksPage />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByTestId("task-row-task-1-edit"));

    expect(await screen.findByTestId("task-edit-dialog")).toBeTruthy();
    expect(((await screen.findByTestId("task-edit-title")) as HTMLInputElement).value).toBe(
      "Migrar el esquema",
    );
  });
});
