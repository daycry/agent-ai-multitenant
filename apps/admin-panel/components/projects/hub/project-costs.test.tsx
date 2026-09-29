// @vitest-environment jsdom
/**
 * La pestaña Costes del proyecto (`task_ui_12`): gasto por plan, agente y modelo
 * contra el presupuesto, y la pausa por presupuesto (con su botón para levantarla).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import { ProjectCosts } from "@/components/projects/hub/project-costs";
import { LanguageProvider } from "@/lib/lang-context";
import type { ProjectCostsData } from "@/lib/projects/project-hub";

configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const PID = "11111111-2222-3333-4444-555555555555";

const DATA: ProjectCostsData = {
  window_days: 90,
  run_count: 3,
  total_cost_usd: "12.500000",
  total_tokens: 3000,
  budget: {
    available: true,
    scope: "project",
    project_id: PID,
    project_name: "Web",
    budget_usd: "100",
    spend_usd: "42",
    percent_used: 42,
    status: "ok",
    over_budget: false,
    period: "monthly",
  },
  paused_by_budget: false,
  by_plan: [{ plan_id: "pl1", title: "Migrar login", runs: 3, cost_usd: "12.5", tokens: 3000 }],
  by_agent: [{ agent_id: "a1", name: "Ana", runs: 3, cost_usd: "12.5", tokens: 3000 }],
  by_model: [
    { model: "claude-x", runs: 2, cost_usd: "10", tokens: 2000 },
    { model: null, runs: 1, cost_usd: "2.5", tokens: 1000 },
  ],
};

function renderCosts(data: ProjectCostsData) {
  apiFetchMock.mockImplementation(async (path: string) => {
    if (path.startsWith("/budgets/pause/override")) return { cleared: true };
    return data;
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProjectCosts projectId={PID} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("ProjectCosts", () => {
  it("pide los costes de ESE proyecto", async () => {
    renderCosts(DATA);
    await screen.findByTestId("costs-total");
    expect(apiFetchMock.mock.calls[0]?.[0]).toBe(`/projects/${PID}/costs`);
    expect(screen.getByTestId("costs-total").textContent).toContain("12.500000");
  });

  it("desglosa por plan, agente y modelo, con el modelo nulo rotulado", async () => {
    renderCosts(DATA);
    expect((await screen.findByTestId("costs-by-plan")).textContent).toContain("Migrar login");
    expect(screen.getByTestId("costs-by-agent").textContent).toContain("Ana");
    const models = screen.getByTestId("costs-by-model").textContent ?? "";
    expect(models).toContain("claude-x");
    expect(models).toContain("Sin modelo");
  });

  it("enseña el gasto contra el presupuesto", async () => {
    renderCosts(DATA);
    const budget = await screen.findByTestId("costs-budget");
    expect(budget.textContent).toContain("42% de 100 USD");
  });

  it("sin presupuesto lo dice", async () => {
    renderCosts({ ...DATA, budget: { available: false, reason: "no_budget_configured" } });
    expect(await screen.findByTestId("costs-budget-none")).toBeTruthy();
  });

  it("con el proyecto pausado por presupuesto, avisa y deja levantar la pausa", async () => {
    renderCosts({ ...DATA, paused_by_budget: true });
    expect(await screen.findByTestId("costs-paused")).toBeTruthy();
    fireEvent.click(screen.getByTestId("costs-paused-clear"));
    await waitFor(() => {
      const call = apiFetchMock.mock.calls.find((c) => c[0] === "/budgets/pause/override");
      expect(call?.[1]).toMatchObject({
        method: "POST",
        body: { scope: "project", project_id: PID },
      });
    });
  });

  it("sin pausa no hay aviso ni botón", async () => {
    renderCosts(DATA);
    await screen.findByTestId("costs-total");
    expect(screen.queryByTestId("costs-paused")).toBeNull();
  });
});
