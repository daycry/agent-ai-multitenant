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

  // G5 (ADR 0103): los safeguard_stats del step de finalize se exponen en la
  // cabecera — antes solo vivían en steps_log y medían falsos positivos por SQL.
  it("surfaces the finalize step's safeguard_stats in the header", async () => {
    apiFetchMock.mockResolvedValue(
      execution({
        steps_log: [
          {
            index: 0,
            kind: "node",
            node: "finalize",
            status: "ok",
            summary: "Finalized",
            safeguard_stats: { "nudge:self_check": 2, "trip:research_exhausted": 1 },
          },
        ],
      }),
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("execution-safeguards")).toBeTruthy());
    const text = screen.getByTestId("execution-safeguards").textContent ?? "";
    expect(text).toContain("nudge:self_check ×2");
    expect(text).toContain("trip:research_exhausted ×1");
  });

  it("omits the safeguards metric when no stats fired", async () => {
    apiFetchMock.mockResolvedValue(execution());
    mount();
    await waitFor(() => expect(screen.getByTestId("execution-status")).toBeTruthy());
    expect(screen.queryByTestId("execution-safeguards")).toBeNull();
  });
});
