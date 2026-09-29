// @vitest-environment jsdom
// task_ui_11 — el doble Kanban ACOTADO al proyecto (CLAUDE.md principio 6):
// arriba sólo los planes de ESTE proyecto, abajo sólo las tareas del plan
// seleccionado. Nunca un tablero plano que mezcle planes.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
vi.mock("@/lib/ws", () => ({ useWebSocket: () => {}, wsUrl: (p: string) => `ws://test${p}` }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "proj-1" }) }));
vi.mock("@/components/tasks/task-detail-sheet", () => ({
  TaskDetailSheet: () => null,
}));

import ProjectBoardPage from "@/app/admin/projects/[id]/board/page";

const PLAN_A = { id: "plan-a", title: "Plan A", status: "in_progress", project_id: "proj-1" };
const PLAN_B = { id: "plan-b", title: "Plan B", status: "approved", project_id: "proj-1" };
const PLAN_OTHER = {
  id: "plan-x",
  title: "Plan ajeno",
  status: "in_progress",
  project_id: "proj-2",
};
const task = (id: string, plan_id: string, status = "ready") => ({
  id,
  project_id: "proj-1",
  plan_id,
  title: `Tarea ${id}`,
  status,
  priority: "medium",
  description: null,
  depends_on: [],
});

function wire() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/plans?")) {
      // Defensa doble: aunque el backend devolviera planes de otro proyecto, el
      // tablero del proyecto no debe pintarlos.
      return Promise.resolve([PLAN_A, PLAN_B, PLAN_OTHER]);
    }
    if (path.includes("/tasks")) {
      const planId = new URL(`http://x${path}`).searchParams.get("plan_id");
      return Promise.resolve(
        planId === "plan-a" ? [task("t-a1", "plan-a")] : [task("t-b1", "plan-b", "backlog")],
      );
    }
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectBoardPage />
    </QueryClientProvider>,
  );
}

configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Tablero del proyecto (task_ui_11)", () => {
  it("pide sólo los planes de este proyecto y no pinta los de otro", async () => {
    wire();
    mount();
    await waitFor(() => expect(screen.getByTestId("plan-card-plan-a")).toBeTruthy());
    expect(screen.getByTestId("plan-card-plan-b")).toBeTruthy();
    expect(screen.queryByTestId("plan-card-plan-x")).toBeNull();
    expect(
      apiFetchMock.mock.calls.some(([p]) => String(p).startsWith("/plans?project_id=proj-1&")),
    ).toBe(true);
  });

  it("agrupa las tareas por plan: sólo las del plan seleccionado, y cambian al elegir otro", async () => {
    wire();
    mount();
    await waitFor(() => expect(screen.getByTestId("task-card-t-a1")).toBeTruthy());
    expect(screen.queryByTestId("task-card-t-b1")).toBeNull();
    fireEvent.click(screen.getByTestId("plan-card-plan-b"));
    await waitFor(() => expect(screen.getByTestId("task-card-t-b1")).toBeTruthy());
    expect(screen.queryByTestId("task-card-t-a1")).toBeNull();
  });

  it("no pide el listado de proyectos (ya se está dentro de uno)", async () => {
    wire();
    mount();
    await waitFor(() => expect(screen.getByTestId("plan-card-plan-a")).toBeTruthy());
    expect(apiFetchMock.mock.calls.some(([p]) => String(p).startsWith("/projects?"))).toBe(false);
  });
});
