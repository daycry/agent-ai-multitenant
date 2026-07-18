// @vitest-environment jsdom
// T11 (c8, ciclo-vida) + C2 (runs-visor), sobre el board real con API/WS
// mockeadas:
//   - T11: la fila superior del board gerencial pinta PLANES reales (GET
//     /plans), no proyectos (ADR 0008 satisfecho).
//   - C2: click limpio en una tarjeta abre el panel de detalle; un drag NO lo
//     abre (distinción click-vs-drag, no rompe el drag&drop).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BoardPage />
    </QueryClientProvider>,
  );
}

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
