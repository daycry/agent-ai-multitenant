// @vitest-environment jsdom
// `task_wf_61`: el humano ve QUÉ criterio falló, no solo que la tarea se rechazó.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { latestReviewCriteria, TaskReviewCriteria } from "@/components/tasks/task-review-criteria";

function event(at: number, criteria: unknown, kind = "review_comment") {
  return { id: `e${at}`, at, kind, actor: "agent:reviewer", payload: { criteria } };
}

const PASS_FAIL = [
  { text: "Devuelve 200", passed: true, evidence: "test_ok en verde" },
  { text: "Registra el intento", passed: false, evidence: "no hay logger en el diff" },
];

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TaskReviewCriteria taskId="t-1" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("latestReviewCriteria", () => {
  it("takes the MOST RECENT review, not the first", () => {
    // Una tarea se revisa varias veces: enseñar el desglose de un rechazo ya
    // corregido sería peor que no enseñar ninguno.
    const events = [
      event(1, [{ text: "viejo", passed: false }]),
      event(2, [{ text: "nuevo", passed: true }]),
    ];
    expect(latestReviewCriteria(events)?.[0].text).toBe("nuevo");
  });

  it("skips review events that carry no breakdown", () => {
    // Un reviewer que no lo emitió (modelo que se lo saltó, run anterior a
    // esto) no puede tapar el desglose de un review anterior que sí lo tiene.
    const events = [event(1, PASS_FAIL), event(2, [])];
    expect(latestReviewCriteria(events)).toHaveLength(2);
  });

  it("ignores events of other kinds", () => {
    expect(latestReviewCriteria([event(1, PASS_FAIL, "transition")])).toBeNull();
  });

  it("is null when there is nothing to show", () => {
    expect(latestReviewCriteria([])).toBeNull();
  });
});

describe("TaskReviewCriteria", () => {
  it("shows each criterion with its evidence", async () => {
    apiFetchMock.mockResolvedValueOnce({ events: [event(1, PASS_FAIL)] });
    renderSection();

    const first = await screen.findByTestId("review-criterion-0");
    expect(first.textContent).toContain("Devuelve 200");
    expect(screen.getByTestId("review-criterion-1").textContent).toContain(
      "no hay logger en el diff",
    );
    // El resumen dice cuántos fallaron: es lo primero que se busca al abrir
    // una tarea rechazada.
    expect(screen.getByTestId("task-review-criteria").textContent).toContain("1 de 2 sin cumplir");
  });

  it("stays silent when the reviewer emitted no breakdown", async () => {
    // Nada que enseñar es mejor que un hueco vacío.
    apiFetchMock.mockResolvedValueOnce({ events: [] });
    renderSection();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("task-review-criteria")).toBeNull();
  });

  it("stays silent when the history cannot be read", async () => {
    apiFetchMock.mockRejectedValueOnce(new Error("403"));
    renderSection();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("task-review-criteria")).toBeNull();
  });
});
