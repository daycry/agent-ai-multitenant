// @vitest-environment jsdom
/**
 * El Resumen del proyecto (`task_ui_10`): los bloques de `GET /projects/{id}/overview`,
 * cada uno enlazando a su pestaña, y los avisos por código.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import { ProjectOverview } from "@/components/projects/overview/project-overview";
import type { ProjectOverviewData } from "@/lib/projects/project-overview";
import { LanguageProvider } from "@/lib/lang-context";

configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const PID = "11111111-2222-3333-4444-555555555555";
const BASE = `/admin/projects/${PID}`;

const FULL: ProjectOverviewData = {
  project: { id: PID, name: "Web", status: "active", team_id: null },
  plans: [
    {
      plan_id: "pl1",
      title: "Migrar login",
      status: "in_progress",
      tasks_done: 1,
      tasks_total: 4,
      url_path: `${BASE}/plans/pl1`,
    },
  ],
  runs: {
    running: 2,
    last_failure: {
      task_title: "Tarea rota",
      execution_id: "e9",
      url_path: "/admin/executions/e9",
    },
    url_path: `${BASE}/runs`,
  },
  spend: {
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
  warnings: [
    { kind: "mcp_not_connected", count: 1, names: ["atlassian"], url_path: `${BASE}/capabilities` },
    { kind: "mcp_no_tools", count: 1, names: ["atlassian"], url_path: `${BASE}/capabilities` },
    { kind: "mcp_egress_blocked", count: 2, names: [], url_path: `${BASE}/capabilities` },
    { kind: "repo_not_synced", count: 1, names: [], url_path: BASE },
    { kind: "role_without_agent", count: 1, names: ["qa"], url_path: `${BASE}/team` },
  ],
  activity: [
    {
      execution_id: "e1",
      status: "running",
      task_title: "Escribir tests",
      agent_name: "Backend",
      at: "2026-09-29T10:00:00+00:00",
      url_path: "/admin/executions/e1",
    },
  ],
  generated_at: "2026-09-29T10:00:00+00:00",
};

function renderOverview(data: ProjectOverviewData | Error) {
  apiFetchMock.mockImplementation(() =>
    data instanceof Error ? Promise.reject(data) : Promise.resolve(data),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProjectOverview projectId={PID} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("ProjectOverview", () => {
  it("pide el resumen de ESE proyecto", async () => {
    renderOverview(FULL);
    await screen.findByTestId("project-overview");
    expect(apiFetchMock.mock.calls[0]?.[0]).toBe(`/projects/${PID}/overview`);
  });

  it("pinta planes, runs, gasto y actividad, cada bloque con su enlace", async () => {
    renderOverview(FULL);
    const plans = await screen.findByTestId("overview-plans");
    expect(plans.textContent).toContain("Migrar login");
    expect(plans.textContent).toContain("1 de 4 tareas");
    expect(screen.getByTestId("overview-plans-link").getAttribute("href")).toBe(`${BASE}/board`);

    const runs = screen.getByTestId("overview-runs");
    expect(runs.textContent).toContain("2 en curso");
    expect(runs.textContent).toContain("Tarea rota");
    expect(screen.getByTestId("overview-runs-link").getAttribute("href")).toBe(`${BASE}/runs`);

    expect(screen.getByTestId("overview-spend-percent").textContent).toContain("42%");
    expect(screen.getByTestId("overview-spend-link").getAttribute("href")).toBe(`${BASE}/costs`);

    const activity = screen.getByTestId("overview-activity");
    expect(activity.textContent).toContain("Escribir tests");
    expect(activity.textContent).toContain("Backend");
  });

  it("pinta un aviso por código, enlazado a la pestaña donde se arregla", async () => {
    renderOverview(FULL);
    const warnings = await screen.findByTestId("overview-warnings");
    expect(warnings.textContent).toContain("MCP sin conectar (OAuth): atlassian");
    expect(warnings.textContent).toContain("MCP sin tools importadas: atlassian");
    expect(warnings.textContent).toContain("Egress bloqueado en 2 servidor(es) MCP");
    expect(warnings.textContent).toContain("El repositorio no está sincronizado");
    expect(warnings.textContent).toContain(
      "Roles que piden los planes activos y el equipo no cubre: qa",
    );
    expect(screen.getByTestId("overview-warning-role_without_agent").getAttribute("href")).toBe(
      `${BASE}/team`,
    );
    expect(screen.getByTestId("overview-warning-mcp_not_connected").getAttribute("href")).toBe(
      `${BASE}/capabilities`,
    );
    expect(screen.getByTestId("overview-warning-repo_not_synced").getAttribute("href")).toBe(BASE);
  });

  it("Vault no disponible es «no lo sé», no «sin conectar»", async () => {
    renderOverview({
      ...FULL,
      warnings: [
        {
          kind: "vault_unavailable",
          count: 1,
          names: ["atlassian"],
          url_path: `${BASE}/capabilities`,
        },
      ],
    });
    const warnings = await screen.findByTestId("overview-warnings");
    expect(warnings.textContent).toContain("Vault no disponible): atlassian");
    expect(warnings.textContent).not.toContain("sin conectar");
    expect(screen.getByTestId("overview-warning-vault_unavailable").getAttribute("href")).toBe(
      `${BASE}/capabilities`,
    );
  });

  it("dice que no hay avisos en vez de callar", async () => {
    renderOverview({ ...FULL, warnings: [] });
    expect((await screen.findByTestId("overview-warnings-none")).textContent).toContain(
      "Sin avisos",
    );
  });

  it("no cuenta el gasto a quien no puede verlo, ni lo confunde con «sin presupuesto»", async () => {
    renderOverview({ ...FULL, spend: { available: false, reason: "forbidden" } });
    await screen.findByTestId("overview-plans");
    expect(screen.queryByTestId("overview-spend")).toBeNull();
  });

  it("dice «sin presupuesto» cuando el proyecto no lo tiene", async () => {
    renderOverview({ ...FULL, spend: { available: false, reason: "no_budget_configured" } });
    expect(await screen.findByTestId("overview-spend-none")).toBeTruthy();
  });

  it("muestra los vacíos de planes, runs y actividad", async () => {
    renderOverview({
      ...FULL,
      plans: [],
      runs: { running: 0, last_failure: null, url_path: `${BASE}/runs` },
      activity: [],
    });
    expect(await screen.findByTestId("overview-plans-empty")).toBeTruthy();
    expect(screen.getByTestId("overview-runs-idle")).toBeTruthy();
    expect(screen.getByTestId("overview-activity-empty")).toBeTruthy();
  });

  it("enlaza las otras pantallas del proyecto que ya no tienen entrada propia", async () => {
    renderOverview(FULL);
    await screen.findByTestId("overview-more");
    for (const suffix of [
      "chat",
      "commands",
      "agent-tools-diagnostic",
      "memories",
      "incoming-webhooks",
      "dep-cache",
    ]) {
      expect(screen.getByTestId(`overview-more-${suffix}`).getAttribute("href")).toBe(
        `${BASE}/${suffix}`,
      );
    }
  });

  it("enseña el error de carga", async () => {
    renderOverview(new Error("boom"));
    await waitFor(() => expect(screen.getByTestId("overview-error")).toBeTruthy());
  });
});
