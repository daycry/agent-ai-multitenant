// @vitest-environment jsdom
// La Oficina v1 (ADR 0118): piso 2D del tenant sobre telemetría REAL.
// Mesas = planes con runs activos (GET /runs?verdict=running); banco = agentes
// sin run (GET /agents); puerta del humano = runs needs_human_review. Clic en
// un personaje activo navega a su run real (la Oficina es una lente).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

import OfficePage from "@/app/admin/office/page";

const RUN_RUNNING = {
  id: "run-1",
  verdict: "running",
  agent_id: "ag-1",
  agent_name: "Backend Dev",
  agent_role: "backend_dev",
  task_id: "t-1",
  task_title: "Implementar el endpoint",
  plan_id: "plan-1",
  plan_title: "Plan CI4",
};
const RUN_ESCALATED = {
  id: "run-2",
  verdict: "needs_human_review",
  agent_id: "ag-2",
  agent_name: "QA",
  agent_role: "qa",
  task_id: "t-2",
  task_title: "Validar despliegue",
  plan_id: "plan-1",
  plan_title: "Plan CI4",
};
const AGENTS = [
  { id: "ag-1", name: "Backend Dev", role: "backend_dev" },
  { id: "ag-2", name: "QA", role: "qa" },
  { id: "ag-3", name: "Arquitecta", role: "architect" },
];

function mockApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/runs") && path.includes("verdict=running")) {
      return Promise.resolve([RUN_RUNNING]);
    }
    if (path.startsWith("/runs") && path.includes("needs_human_review")) {
      return Promise.resolve([RUN_ESCALATED]);
    }
    if (path.startsWith("/agents")) return Promise.resolve(AGENTS);
    return Promise.resolve([]);
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OfficePage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("OfficePage (La Oficina v1)", () => {
  it("sienta al agente con run activo en la mesa de su plan, con su tarea en la burbuja", async () => {
    mockApi();
    renderPage();
    const desk = await screen.findByTestId("office-desk-plan-1");
    expect(desk.textContent).toContain("Plan CI4");
    const char = screen.getByTestId("office-agent-ag-1");
    expect(char.textContent).toContain("Backend Dev");
    expect(screen.getByTestId("office-bubble-ag-1").textContent).toContain(
      "Implementar el endpoint",
    );
  });

  it("agentes sin run van al banco (idle) y los escalados a la puerta del humano", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Arquitecta/);
    const bench = screen.getByTestId("office-bench");
    expect(bench.textContent).toContain("Arquitecta");
    const door = screen.getByTestId("office-human-door");
    expect(door.textContent).toContain("QA");
    expect(door.textContent).toContain("Validar despliegue");
  });

  it("clic en un personaje activo abre su run real", async () => {
    mockApi();
    renderPage();
    fireEvent.click(await screen.findByTestId("office-agent-ag-1"));
    expect(pushMock).toHaveBeenCalledWith("/admin/executions/run-1");
  });
});
