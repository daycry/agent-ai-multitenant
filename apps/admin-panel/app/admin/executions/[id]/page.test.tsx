// @vitest-environment jsdom
// D1 (runs-visor): la cabecera-resumen del detalle del run renderiza las
// métricas (estado · iteraciones · tokens · coste) y el botón Cancelar aparece
// SOLO cuando el run está `running`. Render real con react-query y API/WS
// mockeadas.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "e1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// El stream vivo no aplica al render inicial — WS anulado.
vi.mock("@/lib/ws", () => ({
  useWebSocket: () => {},
  wsUrl: (p: string) => `ws://test${p}`,
}));

import ExecutionTimelinePage from "@/app/admin/executions/[id]/page";

function execution(overrides: Record<string, unknown> = {}) {
  return {
    id: "e1",
    task_id: "t1",
    status: "done",
    abort_code: null,
    output: null,
    finish_status: "success",
    steps_log: [],
    iterations: 13,
    total_tokens: 45678,
    total_cost_usd: 0.4321,
    ...overrides,
  };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ExecutionTimelinePage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Timeline de ejecución — cabecera (runs-visor D1)", () => {
  it("renders the summary metrics for a finished run and hides Cancel", async () => {
    apiFetchMock.mockResolvedValue(execution());
    mount();
    await waitFor(() => expect(screen.getByTestId("execution-status")).toBeTruthy());
    expect(screen.getByTestId("execution-iterations").textContent).toBe("13");
    expect(screen.getByTestId("execution-tokens").textContent).toBe((45678).toLocaleString());
    expect(screen.getByTestId("execution-cost").textContent).toBe("$0.4321");
    // Terminado → sin botón Cancelar.
    expect(screen.queryByTestId("execution-cancel-button")).toBeNull();
    // Enlace de vuelta a la lista (parte de D1: la página deja de ser huérfana).
    expect(screen.getByTestId("execution-back-link")).toBeTruthy();
  });

  it("shows the Cancel button only while the run is running", async () => {
    apiFetchMock.mockResolvedValue(execution({ status: "running", finish_status: null }));
    mount();
    await waitFor(() => expect(screen.getByTestId("execution-cancel-button")).toBeTruthy());
  });
});
