// @vitest-environment jsdom
// Leaderboard de configuraciones (ADR 0121): tabla de combinaciones
// modelo×agente sobre GET /runs/leaderboard, ordenada por el backend
// (éxito desc, coste asc). La nota de atribución honesta es obligatoria.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import LeaderboardPage from "@/app/admin/leaderboard/page";

const ROW = {
  model: "gpt-oss:120b",
  agent_id: "ag-1",
  agent_name: "Backend Dev",
  agent_role: "backend_dev",
  runs: 12,
  done: 10,
  escalated: 1,
  aborted: 1,
  success_rate: 0.8333,
  avg_iterations: 9.5,
  avg_cost_usd: 0.42,
  avg_tokens: 15000,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LeaderboardPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LeaderboardPage", () => {
  it("pinta cada combinación con éxito, coste y muestras", async () => {
    apiFetchMock.mockResolvedValueOnce([ROW]);
    renderPage();
    const row = await screen.findByTestId("leaderboard-row-0");
    expect(row.textContent).toContain("gpt-oss:120b");
    expect(row.textContent).toContain("Backend Dev");
    expect(row.textContent).toContain("83%");
    expect(row.textContent).toContain("12");
    // Nota de atribución honesta (ADR 0121) siempre visible.
    expect(screen.getByTestId("leaderboard-attribution-note")).toBeTruthy();
  });

  it("sin combinaciones con muestras suficientes lo dice claro", async () => {
    apiFetchMock.mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByTestId("leaderboard-empty")).toBeTruthy();
  });
});
