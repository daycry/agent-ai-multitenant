// @vitest-environment jsdom
/**
 * La pestaña Runs del proyecto (`task_ui_12`): reutiliza `GET /runs` acotado con
 * `project_id` y filtra por plan, agente y estado.
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

import { ProjectRuns } from "@/components/projects/hub/project-runs";
import { LanguageProvider } from "@/lib/lang-context";
import type { ExecutionRunRow } from "@/lib/runs";

configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const PID = "11111111-2222-3333-4444-555555555555";

const ROW: ExecutionRunRow = {
  id: "run-1",
  created_at: "2026-09-29T10:00:00Z",
  task_id: "t1",
  task_title: "Escribir tests",
  plan_id: "pl1",
  plan_title: "Migrar login",
  agent_id: "ag1",
  agent_name: "Backend",
  agent_role: "backend_dev",
  model: "m-a",
  verdict: "done",
  succeeded: true,
  finish_status: null,
  retry_count: 0,
  duration_ms: 1500,
  total_tokens: 100,
  total_cost_usd: "0.5",
  started_at: null,
  completed_at: null,
  display_currency: null,
  display_cost: null,
  applied_rate: null,
  applied_rate_date: null,
};

function routes(path: string): unknown {
  if (path === `/projects/${PID}/plans`) return [{ id: "pl1", title: "Migrar login" }];
  if (path === "/agents") return [{ id: "ag1", name: "Backend" }];
  if (path.startsWith("/runs")) return [ROW];
  return [];
}

function renderRuns() {
  apiFetchMock.mockImplementation(async (path: string) => routes(path));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProjectRuns projectId={PID} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

const runsCalls = () =>
  apiFetchMock.mock.calls.map((c) => String(c[0])).filter((p) => p.startsWith("/runs"));

describe("ProjectRuns", () => {
  it("lista los runs de ESE proyecto y enlaza cada uno a su detalle", async () => {
    renderRuns();
    const row = await screen.findByTestId("run-row-run-1");
    expect(row.textContent).toContain("Escribir tests");
    expect(runsCalls()[0]).toContain(`project_id=${PID}`);
    expect(screen.getByTestId("run-link-run-1").getAttribute("href")).toBe(
      "/admin/executions/run-1",
    );
  });

  it("filtrar por plan vuelve a pedir /runs con plan_id", async () => {
    renderRuns();
    await screen.findByTestId("run-row-run-1");
    await screen.findByRole("option", { name: "Migrar login" });
    fireEvent.change(screen.getByTestId("project-runs-plan-filter"), { target: { value: "pl1" } });
    await waitFor(() => expect(runsCalls().some((p) => p.includes("plan_id=pl1"))).toBe(true));
    expect(runsCalls().at(-1)).toContain(`project_id=${PID}`);
  });

  it("filtrar por agente y por estado usa agent_id y verdict", async () => {
    renderRuns();
    await screen.findByTestId("run-row-run-1");
    await screen.findByRole("option", { name: "Backend" });
    fireEvent.change(screen.getByTestId("project-runs-agent-filter"), { target: { value: "ag1" } });
    fireEvent.change(screen.getByTestId("project-runs-status-filter"), {
      target: { value: "failed" },
    });
    await waitFor(() => {
      const last = runsCalls().at(-1) ?? "";
      expect(last).toContain("agent_id=ag1");
      expect(last).toContain("verdict=failed");
    });
  });

  it("dice que no hay runs en vez de callar", async () => {
    renderRuns();
    await screen.findByTestId("run-row-run-1");
    apiFetchMock.mockImplementation(async (path: string) =>
      path.startsWith("/runs") ? [] : routes(path),
    );
    fireEvent.change(screen.getByTestId("project-runs-status-filter"), {
      target: { value: "aborted" },
    });
    expect(await screen.findByTestId("project-runs-empty")).toBeTruthy();
  });
});
