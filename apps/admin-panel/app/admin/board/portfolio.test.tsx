// @vitest-environment jsdom
// task_ui_11 — /admin/board es el PORTFOLIO: planes de todos los proyectos, con
// la columna Proyecto siempre visible, filtros por estado/proyecto/responsable y
// enlace al tablero de cada proyecto. Nunca un Kanban plano de tareas.

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

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
vi.mock("@/lib/ws", () => ({ useWebSocket: () => {}, wsUrl: (p: string) => `ws://test${p}` }));

import BoardPage from "@/app/admin/board/page";

const PROJECTS = [
  { id: "proj-1", name: "Alfa" },
  { id: "proj-2", name: "Beta" },
];
const PLANS = [
  {
    id: "p1",
    title: "Plan uno",
    status: "in_progress",
    project_id: "proj-1",
    created_by: "aaaaaaaa-1111",
  },
  {
    id: "p2",
    title: "Plan dos",
    status: "blocked",
    project_id: "proj-2",
    created_by: "bbbbbbbb-2222",
  },
  {
    id: "p3",
    title: "Plan tres",
    status: "in_progress",
    project_id: "proj-2",
    created_by: "aaaaaaaa-1111",
  },
];

function mount() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/plans?")) return Promise.resolve(PLANS);
    if (path.startsWith("/projects?")) return Promise.resolve(PROJECTS);
    return Promise.resolve([]);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BoardPage />
    </QueryClientProvider>,
  );
}

const visibleRows = () =>
  Array.from(document.querySelectorAll('[data-testid^="portfolio-row-"]')).map((r) =>
    r.getAttribute("data-testid"),
  );

configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Portfolio de planes (task_ui_11)", () => {
  it("lista planes de todos los proyectos con la columna Proyecto siempre visible", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("portfolio-row-p1")).toBeTruthy());
    expect(screen.getByTestId("portfolio-col-project")).toBeTruthy();
    expect(visibleRows()).toHaveLength(3);
    expect(screen.getByTestId("portfolio-project-p1").textContent).toBe("Alfa");
    expect(screen.getByTestId("portfolio-project-p2").textContent).toBe("Beta");
  });

  it("no pinta tareas: no es un Kanban plano", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("portfolio-row-p1")).toBeTruthy());
    expect(screen.queryByTestId("board-columns")).toBeNull();
    expect(apiFetchMock.mock.calls.some(([p]) => String(p).includes("/tasks"))).toBe(false);
  });

  it("cada plan enlaza al tablero de su proyecto", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("portfolio-row-p2")).toBeTruthy());
    const link = within(screen.getByTestId("portfolio-row-p2")).getByTestId(
      "portfolio-board-link-p2",
    );
    expect(link.getAttribute("href")).toBe("/admin/projects/proj-2/board");
  });

  it("filtra por estado, por proyecto y por responsable, y se combinan", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("portfolio-row-p1")).toBeTruthy());

    fireEvent.change(screen.getByTestId("portfolio-filter-status"), {
      target: { value: "blocked" },
    });
    expect(visibleRows()).toEqual(["portfolio-row-p2"]);

    fireEvent.change(screen.getByTestId("portfolio-filter-status"), { target: { value: "" } });
    fireEvent.change(screen.getByTestId("portfolio-filter-project"), {
      target: { value: "proj-2" },
    });
    expect(visibleRows()).toEqual(["portfolio-row-p2", "portfolio-row-p3"]);

    fireEvent.change(screen.getByTestId("portfolio-filter-owner"), {
      target: { value: "aaaaaaaa-1111" },
    });
    expect(visibleRows()).toEqual(["portfolio-row-p3"]);
  });

  it("un filtro sin coincidencias muestra el vacío, no una tabla en blanco", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("portfolio-row-p1")).toBeTruthy());
    fireEvent.change(screen.getByTestId("portfolio-filter-status"), {
      target: { value: "archived" },
    });
    expect(screen.getByTestId("portfolio-empty")).toBeTruthy();
  });
});
