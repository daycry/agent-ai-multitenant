// @vitest-environment jsdom
// `task_wf_52b`: el desglose por item de una corrida.
//
// Las filas `eval_results` se escribían desde el Plan 14 y ninguna pantalla las
// leía: el historial decía «pass rate 60 %» y ahí acababa. Lo que se prueba
// aquí es que un fallo quede EXPLICADO — qué criterio, con qué nota y con la
// justificación del juez.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { EvalRunResults, formatScore, truncateOutput } from "@/components/evals/eval-run-results";

const FAILING = {
  id: "r-1",
  run_id: "run-1",
  item_id: "i-1",
  produced_output: "def login(): pass",
  criterion_scores: [
    {
      criterion_id: "c-1",
      name: "correccion",
      score: 0.2,
      passed: false,
      rationale: "no valida la contraseña",
    },
  ],
  verdict: "fail",
  overall_score: "0.200",
  latency_ms: 1200,
  tokens: 340,
  cost_usd: "0.004",
  created_at: "2026-07-25T10:00:00Z",
};

function renderResults() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <EvalRunResults runId="run-1" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EvalRunResults", () => {
  it("un fallo llega EXPLICADO: criterio, nota y por qué", async () => {
    apiFetchMock.mockResolvedValue([FAILING]);
    renderResults();

    await waitFor(() => expect(screen.getByTestId("eval-run-results")).toBeTruthy());
    const card = screen.getByTestId("eval-result-r-1");
    expect(card.textContent).toContain("fail");
    expect(card.textContent).toContain("20%");
    // Sin el nombre, el desglose sería una lista de UUIDs y no diría QUÉ falló.
    expect(card.textContent).toContain("correccion");
    expect(card.textContent).toContain("no valida la contraseña");
    expect(card.textContent).toContain("def login(): pass");
  });

  it("pide el desglose de la corrida que se le pasa", async () => {
    apiFetchMock.mockResolvedValue([]);
    renderResults();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith("/eval-runs/run-1/results"));
  });

  it("una corrida sin items lo dice en vez de fingir un vacío", async () => {
    apiFetchMock.mockResolvedValue([]);
    renderResults();
    await waitFor(() => expect(screen.getByTestId("eval-run-results-empty")).toBeTruthy());
  });

  it("un fallo de carga no se traga en silencio", async () => {
    apiFetchMock.mockRejectedValue(new Error("boom"));
    renderResults();
    await waitFor(() => expect(screen.getByTestId("eval-run-results-error")).toBeTruthy());
  });

  it("un criterio retirado del dataset no rompe la tarjeta", async () => {
    apiFetchMock.mockResolvedValue([
      { ...FAILING, criterion_scores: [{ criterion_id: "c-x", name: "(criterio retirado)" }] },
    ]);
    renderResults();
    await waitFor(() => expect(screen.getByTestId("eval-result-r-1")).toBeTruthy());
    expect(screen.getByTestId("eval-result-r-1").textContent).toContain("(criterio retirado)");
  });
});

describe("formatScore", () => {
  it("convierte la cadena decimal del backend en porcentaje", () => {
    expect(formatScore("0.750")).toBe("75%");
    expect(formatScore(1)).toBe("100%");
  });

  it("un cero es 0%, no un guion — medir cero y no medir son distintos", () => {
    expect(formatScore("0.000")).toBe("0%");
    expect(formatScore(0)).toBe("0%");
  });

  it("lo que no hay se dice que no hay", () => {
    expect(formatScore(null)).toBe("—");
    expect(formatScore(undefined)).toBe("—");
    expect(formatScore("no-es-un-numero")).toBe("—");
  });
});

describe("truncateOutput", () => {
  it("aplana saltos de línea para que quepa en una fila", () => {
    expect(truncateOutput("a\n\n  b")).toBe("a b");
  });

  it("recorta lo largo dejando marca de que hay más", () => {
    const out = truncateOutput("x".repeat(300));
    expect(out.length).toBe(161);
    expect(out.endsWith("…")).toBe(true);
  });

  it("sin salida no inventa nada", () => {
    expect(truncateOutput(null)).toBe("—");
  });
});
