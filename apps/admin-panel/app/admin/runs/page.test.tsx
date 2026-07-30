// @vitest-environment jsdom
// B2 (runs-visor): la lista global /admin/runs renderiza filas desde la API
// (recientes primero, formateo compartido) y una fila `running` muestra el
// estado vivo SIN tokens/coste falsos (las métricas se persisten al finalizar).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExecutionRunRow } from "@/lib/runs";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

const listRunsMock = vi.fn();
vi.mock("@/lib/runs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/runs")>();
  return { ...actual, listRuns: (...args: unknown[]) => listRunsMock(...args) };
});

import RunsPage from "@/app/admin/runs/page";
import { LanguageProvider } from "@/lib/lang-context";

function row(overrides: Partial<ExecutionRunRow>): ExecutionRunRow {
  return {
    id: "r1",
    created_at: "2026-07-08T10:00:00Z",
    task_id: "t1",
    task_title: "Tarea A",
    plan_id: "p1",
    plan_title: "Plan CI4",
    agent_id: "a1",
    agent_name: "Backend Dev",
    agent_role: "backend",
    model: "claude-opus",
    verdict: "done",
    succeeded: true,
    finish_status: "success",
    retry_count: 0,
    duration_ms: 61500,
    total_tokens: 250000,
    total_cost_usd: "1.2345",
    started_at: null,
    completed_at: null,
    display_currency: null,
    display_cost: null,
    applied_rate: null,
    applied_rate_date: null,
    ...overrides,
  };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <LanguageProvider>
      <QueryClientProvider client={client}>
        <RunsPage />
      </QueryClientProvider>
    </LanguageProvider>,
  );
}

afterEach(() => {
  cleanup();
  listRunsMock.mockReset();
});

describe("/admin/runs (runs-visor B2)", () => {
  it("renders one row per run with the shared formatting", async () => {
    listRunsMock.mockResolvedValue([row({ id: "r1" })]);
    mount();
    await waitFor(() => expect(screen.getByTestId("run-row-r1")).toBeTruthy());
    const text = screen.getByTestId("run-row-r1").textContent ?? "";
    expect(text).toContain("Tarea A");
    expect(text).toContain("Backend Dev");
    expect(text).toContain((250000).toLocaleString());
    expect(text).toContain("$1.2345");
  });

  it("a running row shows live status WITHOUT fake zero metrics", async () => {
    listRunsMock.mockResolvedValue([
      row({
        id: "r2",
        verdict: "running",
        succeeded: false,
        finish_status: null,
        duration_ms: null,
        total_tokens: 0,
        total_cost_usd: "0",
      }),
    ]);
    mount();
    await waitFor(() => expect(screen.getByTestId("run-row-r2")).toBeTruthy());
    const text = screen.getByTestId("run-row-r2").textContent ?? "";
    // Estado vivo visible, métricas en «—» (no un falso 0).
    expect(text).toContain("En curso");
    expect(text).toContain("———");
    expect(text).not.toContain("$0.0000");
  });

  it("shows the empty state when the tenant has no runs", async () => {
    listRunsMock.mockResolvedValue([]);
    mount();
    await waitFor(() => expect(screen.getByTestId("runs-empty")).toBeTruthy());
  });
});
