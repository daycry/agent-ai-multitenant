// @vitest-environment jsdom
// Carril D: la ficha de coste tarificaba con el modelo por defecto sin decir
// que lo estaba haciendo. El número de relleno se veía exactamente igual que el
// medido, y ésa es la única diferencia que le importa a quien lo lee.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  CostBreakdownSection,
  pricedWithDefaultModelOnly,
} from "@/app/admin/projects/[id]/plans/[planId]/plan-cost-section";

/** Una fila de coste IA con lo mínimo que el componente lee. */
function aiTask(taskId: string, modelId: string) {
  return {
    task_id: taskId,
    title: `Tarea ${taskId}`,
    complexity: "m",
    model_id: modelId,
    tokens_in_min: 100,
    tokens_in_max: 300,
    tokens_out_min: 50,
    tokens_out_max: 150,
    cost_min: "0.0100",
    cost_max: "0.0300",
  };
}

function costResponse(models: string[]) {
  return {
    human: {
      currency: "EUR",
      hourly_rate: "50.00",
      total_hours: "8.000",
      total_cost: "400.00",
      tasks: models.map((_, i) => ({
        task_id: `t${i + 1}`,
        title: `Tarea t${i + 1}`,
        hours: "4.000",
        cost: "200.00",
      })),
    },
    ai: {
      currency: "USD",
      default_model_id: "gpt-4o",
      cost_min: "0.0200",
      cost_max: "0.0600",
      tasks: models.map((model, i) => aiTask(`t${i + 1}`, model)),
      missing_models: [] as string[],
    },
  };
}

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CostBreakdownSection planId="p-1" projectId="proj-1" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("pricedWithDefaultModelOnly", () => {
  it("es cierto cuando ninguna fila trae un modelo distinto del por defecto", () => {
    expect(pricedWithDefaultModelOnly(costResponse(["gpt-4o", "gpt-4o"]).ai)).toBe(true);
  });

  it("es falso en cuanto UNA tarea resuelve a otro modelo", () => {
    // El caso medido de verdad: el plan que tarifica sus tareas con
    // `claude-opus-4-8` porque el equipo resuelve la cadena del ADR 0065.
    expect(pricedWithDefaultModelOnly(costResponse(["claude-opus-4-8", "gpt-4o"]).ai)).toBe(false);
  });

  it("no avisa de un plan sin tareas: de eso ya habla el estado vacío", () => {
    expect(pricedWithDefaultModelOnly(costResponse([]).ai)).toBe(false);
  });
});

describe("CostBreakdownSection", () => {
  it("avisa —y dice dónde se arregla— cuando todo se estima con el modelo por defecto", async () => {
    apiFetchMock.mockResolvedValueOnce(costResponse(["gpt-4o", "gpt-4o"]));
    renderSection();

    const warning = await screen.findByTestId("plan-cost-ai-default-only");
    // El modelo va interpolado: sin él, el aviso no dice CON QUÉ se ha cobrado.
    expect(warning.textContent).toContain("gpt-4o");
    // Y la causa accionable, que es lo que distingue el aviso de un disclaimer.
    expect(warning.textContent).toContain("equipo");
    expect(screen.getByTestId("plan-cost-ai-default-only-link").getAttribute("href")).toBe(
      "/admin/projects/proj-1",
    );
  });

  it("no avisa cuando el modelo lo resolvió la cadena del ADR 0065", async () => {
    apiFetchMock.mockResolvedValueOnce(costResponse(["claude-opus-4-8", "gpt-oss:120b"]));
    renderSection();

    // Se espera a que la tabla esté pintada; si no, la ausencia del aviso sería
    // sólo la de un componente que aún no ha resuelto su query.
    await screen.findByTestId("plan-cost-ai");
    expect(screen.queryByTestId("plan-cost-ai-default-only")).toBeNull();
  });

  it("no avisa en un plan sin tareas", async () => {
    apiFetchMock.mockResolvedValueOnce(costResponse([]));
    renderSection();

    await screen.findByTestId("plan-cost-breakdown-empty");
    expect(screen.queryByTestId("plan-cost-ai-default-only")).toBeNull();
  });
});
