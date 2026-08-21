// @vitest-environment jsdom
// remediacion-gestion-proyectos-2026-07-25 / task_wf_30 — la cabecera de estado
// del plan RENDERIZA lo que el backend calcula.
//
// El backend está cubierto entero y los tres helpers puros tienen sus tests
// (`plan-status-header.test.ts(x)`), pero nadie afirmaba que los testids
// existieran en el DOM: `plan-status-progress-label`, `plan-status-pr-link` y
// `plan-status-cost-actual` tenían CERO ocurrencias en `*.test.tsx`. Es
// exactamente el defecto que la remediación persigue (D-01/D-02/D-04: cosas
// calculadas y nunca conectadas a su consumidor) — un test de los helpers pasa
// igual de verde con la cabecera entera borrada.
//
// Incluye el caso "PR sin url", que es el estado por defecto de todo plan que aún
// no lo abrió y el que más veces se ve.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  PlanStatusHeader,
  type PlanStatusResponse,
} from "@/app/admin/projects/[id]/plans/[planId]/plan-status-header";

function status(overrides: Partial<PlanStatusResponse> = {}): PlanStatusResponse {
  return {
    plan_id: "plan-1",
    status: "completed",
    progress: { total: 8, done: 8, open: 0, label: "8/8" },
    pr: {
      url: "https://github.com/acme/repo/pull/42",
      branch: "plan/ab12cd34-refactor",
      error: null,
    },
    cost: {
      ai_currency: "USD",
      human_currency: "EUR",
      estimated_ai_min: "1.000000",
      estimated_ai_max: "4.000000",
      estimated_human_hours: "12.0",
      estimated_human_cost: "480.000000",
      actual_ai_cost: "2.345678",
      actual_tokens: 812_345,
      actual_runs: 7,
      over_estimate: false,
    },
    ...overrides,
  };
}

function mount(data: PlanStatusResponse) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/plans/plan-1/status") return Promise.resolve(data);
    return Promise.resolve({});
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PlanStatusHeader planId="plan-1" />
    </QueryClientProvider>,
  );
}

// Los `waitFor` de este fichero esperan transiciones de TanStack Query. El
// timeout por defecto de RTL (1s) se queda corto cuando la suite corre entera en
// paralelo y la máquina va cargada: se vio un rojo fantasma así. Se sube aquí
// (por fichero) en vez de tocar la config compartida.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("PlanStatusHeader — el plan cerrado enseña progreso, PR y coste real", () => {
  it("pinta el progreso servido y su porcentaje", async () => {
    mount(status());
    await waitFor(() => expect(screen.getByTestId("plan-status-header")).toBeTruthy());
    // La etiqueta viene del backend (`compute_plan_progress`); la UI no la inventa.
    expect(screen.getByTestId("plan-status-progress-label").textContent).toBe("8/8");
    expect(screen.getByTestId("plan-status-progress").textContent).toContain("100%");
    expect(apiFetchMock).toHaveBeenCalledWith("/plans/plan-1/status");
  });

  it("el PR es un enlace real a su url, etiquetado con la rama", async () => {
    mount(status());
    await waitFor(() => expect(screen.getByTestId("plan-status-pr-link")).toBeTruthy());
    const link = screen.getByTestId("plan-status-pr-link") as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("https://github.com/acme/repo/pull/42");
    expect(link.textContent).toContain("plan/ab12cd34-refactor");
    // Se abre fuera del panel y sin filtrar el referrer.
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    // Y no se pinta a la vez el estado "todavía sin PR".
    expect(screen.queryByTestId("plan-status-pr-none")).toBeNull();
  });

  it("pinta el coste REAL agregado junto al estimado", async () => {
    mount(status());
    await waitFor(() => expect(screen.getByTestId("plan-status-cost-actual")).toBeTruthy());
    // D-04: el real se agregaba y no se veía. 2.345678 USD → dos decimales.
    expect(screen.getByTestId("plan-status-cost-actual").textContent).toBe("2.35 USD");
    const cost = screen.getByTestId("plan-status-cost").textContent ?? "";
    expect(cost).toContain("4.00 USD"); // el estimado máximo, como referencia
    expect(cost).toContain("812,3k tokens");
    expect(cost).toContain("7 runs");
    expect(cost).toContain("480.00 EUR"); // estimación humana
    // Dentro de presupuesto → sin insignia de exceso.
    expect(screen.queryByTestId("plan-status-over-estimate")).toBeNull();
  });

  it("marca el exceso de presupuesto cuando el backend lo dice", async () => {
    mount(
      status({
        cost: { ...status().cost, actual_ai_cost: "9.500000", over_estimate: true },
      }),
    );
    await waitFor(() => expect(screen.getByTestId("plan-status-over-estimate")).toBeTruthy());
    expect(screen.getByTestId("plan-status-cost-actual").textContent).toBe("9.50 USD");
  });

  describe("PR sin url", () => {
    it("sin PR y sin error dice «Todavía sin PR» en vez de un enlace muerto", async () => {
      mount(status({ pr: { url: null, branch: "plan/ab12cd34-refactor", error: null } }));
      await waitFor(() => expect(screen.getByTestId("plan-status-pr-none")).toBeTruthy());
      // Lo importante: NO se renderiza un <a> sin href (ni con la rama como href).
      expect(screen.queryByTestId("plan-status-pr-link")).toBeNull();
      expect(screen.getByTestId("plan-status-pr-none").textContent).toContain("Todavía sin PR");
    });

    it("si la apertura del PR falló, enseña el motivo (D-02)", async () => {
      // Este era el agujero: se aprobaba el plan y no se veía ni el PR ni, si
      // falló, por qué.
      mount(
        status({
          pr: { url: null, branch: "plan/ab12cd34", error: "no such remote 'origin'" },
        }),
      );
      await waitFor(() => expect(screen.getByTestId("plan-status-pr-error")).toBeTruthy());
      expect(screen.getByTestId("plan-status-pr-error").textContent).toContain(
        "no such remote 'origin'",
      );
      expect(screen.queryByTestId("plan-status-pr-link")).toBeNull();
      expect(screen.queryByTestId("plan-status-pr-none")).toBeNull();
    });
  });

  it("un plan sin tareas no se lee como 0% hecho", async () => {
    mount(status({ progress: { total: 0, done: 0, open: 0, label: "0/0" } }));
    await waitFor(() => expect(screen.getByTestId("plan-status-progress")).toBeTruthy());
    expect(screen.getByTestId("plan-status-progress").textContent).toContain("sin tareas todavía");
    expect(screen.getByTestId("plan-status-progress").textContent).not.toContain("0%");
  });

  it("mientras carga muestra su propio placeholder, no una cabecera vacía", async () => {
    apiFetchMock.mockImplementation(() => new Promise(() => {}));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PlanStatusHeader planId="plan-1" />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("plan-status-header-loading")).toBeTruthy();
    expect(screen.queryByTestId("plan-status-header")).toBeNull();
  });
});
