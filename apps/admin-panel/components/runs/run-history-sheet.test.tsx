// @vitest-environment jsdom
// C1 (runs-visor): el panel de historial de runs de una tarea — mapea runs a
// filas (recientes primero, formateo compartido) y muestra el estado vacío
// «sin ejecuciones todavía». Render real con react-query y la API mockeada.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExecutionRunRow } from "@/lib/runs";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), prefetch: vi.fn() }),
}));

const listRunsMock = vi.fn();
vi.mock("@/lib/runs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/runs")>();
  return { ...actual, listRuns: (...args: unknown[]) => listRunsMock(...args) };
});

import { RunHistorySheet } from "@/components/runs/run-history-sheet";
import { LanguageProvider } from "@/lib/lang-context";

function row(overrides: Partial<ExecutionRunRow>): ExecutionRunRow {
  return {
    id: "r1",
    created_at: "2026-07-08T10:00:00Z",
    task_id: "t1",
    task_title: "T",
    plan_id: null,
    plan_title: null,
    agent_id: null,
    agent_name: "Backend Dev",
    agent_role: null,
    model: "claude-opus",
    verdict: "done",
    succeeded: true,
    finish_status: null,
    retry_count: 0,
    duration_ms: 1500,
    total_tokens: 1234,
    total_cost_usd: "0.1234",
    started_at: null,
    completed_at: null,
    display_currency: null,
    display_cost: null,
    applied_rate: null,
    applied_rate_date: null,
    ...overrides,
  };
}

function mount(taskId: string | null = "t1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <LanguageProvider>
      <QueryClientProvider client={client}>
        <RunHistorySheet taskId={taskId} taskTitle="Tarea X" open onOpenChange={() => {}} />
      </QueryClientProvider>
    </LanguageProvider>,
  );
}

afterEach(() => {
  cleanup();
  listRunsMock.mockReset();
  push.mockReset();
});

describe("RunHistorySheet (runs-visor C1)", () => {
  it("renders one row per run with the shared formatting", async () => {
    listRunsMock.mockResolvedValue([
      row({ id: "r1", verdict: "done" }),
      row({ id: "r2", verdict: "running", duration_ms: null, total_tokens: 0 }),
    ]);
    mount();
    await waitFor(() => expect(screen.getByTestId("run-history-row-r1")).toBeTruthy());
    expect(listRunsMock).toHaveBeenCalledWith({ task_id: "t1", limit: 50 });
    // Fila terminada: duración + tokens + coste formateados.
    expect(screen.getByTestId("run-history-row-r1").textContent).toContain("1.5 s");
    expect(screen.getByTestId("run-history-row-r1").textContent).toContain("$0.1234");
    // Fila running: estado vivo SIN métricas falsas (— en vez de 0).
    const running = screen.getByTestId("run-history-row-r2");
    expect(running.textContent).toContain("running");
    expect(running.textContent).toContain("— · — tok");
  });

  it("shows the empty state when the task was never executed", async () => {
    listRunsMock.mockResolvedValue([]);
    mount();
    await waitFor(() => expect(screen.getByTestId("run-history-empty")).toBeTruthy());
    expect(screen.getByTestId("run-history-empty").textContent).toContain(
      "no tiene ejecuciones todavía",
    );
  });
});
