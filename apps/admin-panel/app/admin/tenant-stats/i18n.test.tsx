// @vitest-environment jsdom

/**
 * `tenant-stats`, migrada al diccionario y partida en secciones (plan prod-16,
 * `task_prod16_03` + `task_prod16_08`).
 *
 * La pantalla tenía 861 líneas y todo el texto cableado en castellano: con el
 * toggle en EN un operador anglófono leía "Explorador de runs", "Coste mínimo
 * USD" y "Agentes bottom (tasa de éxito)". Aquí se afirman las CUATRO piezas en
 * que se partió (cuerpo, segmentación de coste, explorador y visuales) en los
 * dos idiomas: el troceado es refactor mecánico, así que si una sección se
 * quedó sin traducir o dejó de renderizarse, salta.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    user: { user_id: "u1", email: "a@a.test", full_name: "A", is_system_admin: false },
    isLoading: false,
    isError: false,
    isSystemAdmin: false,
    isSystemOwner: false,
    isTenantAdmin: true,
    isTenantMember: true,
    roleInActiveTenant: "tenant_admin",
  }),
}));

import TenantStatsPage from "@/app/admin/tenant-stats/page";

const STORAGE_KEY = "admin-panel.lang";

const AGENT = {
  agent_id: "a1",
  agent_name: "Backend Dev",
  agent_role: "backend",
  run_count: 4,
  succeeded: 3,
  success_rate: "0.75",
  mean_duration_ms: "1200",
  mean_cost_usd: "0.10",
  total_cost_usd: "0.40",
  total_tokens: 900,
};

function routeApi(path: string): unknown {
  if (path.startsWith("/tenant-stats/dashboard")) {
    return {
      window_days: 90,
      currency: "USD",
      total_runs: 4,
      succeeded_runs: 3,
      overall_success_rate: "0.75",
      mean_duration_ms: "1200",
      mean_cost_usd: "0.10",
      total_cost_usd: "0.40",
      by_agent: [AGENT],
      top_agents: [AGENT],
      bottom_agents: [AGENT],
      trend: [
        {
          day: "2026-07-30",
          run_count: 4,
          succeeded: 3,
          success_rate: "0.75",
          total_cost_usd: "0.40",
        },
      ],
    };
  }
  if (path.startsWith("/tenant-stats/consumption")) {
    return {
      window_days: 90,
      currency: "USD",
      run_count: 4,
      accumulated_cost_usd: "0.40",
      mean_cost_usd: "0.10",
      total_tokens: 900,
      total_tokens_input: 600,
      total_tokens_output: 300,
      total_tokens_cached: 0,
      costliest_run: {
        execution_id: "e1",
        task_id: "t1",
        task_title: "Migrar i18n",
        agent_name: "Backend Dev",
        total_cost_usd: "0.20",
        total_tokens: 500,
        created_at: "2026-07-30T10:00:00Z",
      },
      ai_cost_usd: "0.30",
      human_cost_usd: "0.10",
      total_cost_usd: "0.40",
      human_hours_logged: "2.0",
    };
  }
  if (path.startsWith("/tenant-stats/runs")) return [];
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation((path: string) => Promise.resolve(routeApi(path)));
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <TenantStatsPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("tenant-stats en castellano", () => {
  it("rinde cabecera, cuerpo, segmentación de coste y explorador", async () => {
    renderIn("es");

    expect(await screen.findByText("Estadísticas")).toBeDefined();
    expect(await screen.findByText("Tendencia de tasa de éxito (diaria)")).toBeDefined();
    expect(screen.getByText("Agentes top (tasa de éxito)")).toBeDefined();
    expect(screen.getByText("Por agente")).toBeDefined();
    expect(await screen.findByText("Segmentación de coste: IA vs Humano")).toBeDefined();
    expect(screen.getByText("Run más costoso")).toBeDefined();
    expect(screen.getByText("Explorador de runs")).toBeDefined();
    expect(screen.getByPlaceholderText("Coste mínimo USD")).toBeDefined();
    expect(screen.getByRole("button", { name: "Siguiente" })).toBeDefined();
  });
});

describe("tenant-stats en inglés", () => {
  it("rinde las mismas cuatro secciones traducidas", async () => {
    renderIn("en");

    expect(await screen.findByText("Statistics")).toBeDefined();
    expect(await screen.findByText("Success-rate trend (daily)")).toBeDefined();
    expect(screen.getByText("Top agents (success rate)")).toBeDefined();
    expect(screen.getByText("By agent")).toBeDefined();
    expect(await screen.findByText("Cost breakdown: AI vs human")).toBeDefined();
    expect(screen.getByText("Costliest run")).toBeDefined();
    expect(screen.getByText("Runs explorer")).toBeDefined();
    expect(screen.getByPlaceholderText("Min cost USD")).toBeDefined();
    expect(screen.getByRole("button", { name: "Next" })).toBeDefined();
  });

  it("no deja castellano por debajo en ninguna de las secciones", async () => {
    renderIn("en");

    await screen.findByText("Statistics");
    await screen.findByText("Cost breakdown: AI vs human");

    expect(screen.queryByText("Estadísticas")).toBeNull();
    expect(screen.queryByText("Explorador de runs")).toBeNull();
    expect(screen.queryByPlaceholderText("Coste mínimo USD")).toBeNull();
    expect(screen.queryByText("Por agente")).toBeNull();
    expect(screen.queryByRole("button", { name: "Siguiente" })).toBeNull();
  });

  it("traduce las cabeceras de las dos tablas y el aria-label del sparkline", async () => {
    renderIn("en");

    expect(await screen.findByLabelText("Success rate per day")).toBeDefined();
    // Cabecera de la tabla por agente.
    expect(screen.getByText("Success")).toBeDefined();
    // Cabecera del explorador de runs (la tabla sólo aparece con filas, así que
    // se comprueba el estado vacío, que también es texto de UI).
    expect(await screen.findByText("No runs for these filters.")).toBeDefined();
  });
});
