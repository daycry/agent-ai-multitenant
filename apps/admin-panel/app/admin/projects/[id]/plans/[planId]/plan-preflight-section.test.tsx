// @vitest-environment jsdom
// `task_wf_72`: el semáforo antes de aprobar.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  planNeedsPreflight,
  PlanPreflightSection,
} from "@/app/admin/projects/[id]/plans/[planId]/plan-preflight-section";

const CLEAN = {
  task_count: 4,
  blockers: 0,
  warnings: 0,
  critical_path: ["a", "b"],
  critical_path_length: 2,
  max_parallelism: 3,
  findings: [],
};

const DIRTY = {
  ...CLEAN,
  blockers: 1,
  warnings: 1,
  findings: [
    {
      code: "role_without_agent",
      severity: "blocker",
      message: "2 tarea(s) piden un rol que el equipo no tiene",
      task_ids: ["t2", "t7"],
    },
    {
      code: "task_without_criteria",
      severity: "warning",
      message: "1 tarea(s) sin criterios de aceptación",
      task_ids: ["t3"],
    },
  ],
};

function renderSection(status = "pending_approval") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PlanPreflightSection planId="p-1" status={status} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("planNeedsPreflight", () => {
  it("only asks before the plan is signed", () => {
    // Después de firmar, el semáforo sería un reproche sobre una decisión ya
    // tomada, no una ayuda para tomarla.
    for (const status of ["draft", "pending_approval", "pending_second_approval"]) {
      expect(planNeedsPreflight(status)).toBe(true);
    }
    for (const status of ["approved", "in_progress", "completed", "cancelled"]) {
      expect(planNeedsPreflight(status)).toBe(false);
    }
  });
});

describe("PlanPreflightSection", () => {
  it("names each problem and the tasks it points at", async () => {
    // Un aviso sin diana obliga a buscar la tarea a mano, que es justo el
    // trabajo que el preflight viene a ahorrar.
    apiFetchMock.mockResolvedValueOnce(DIRTY);
    renderSection();

    const findings = await screen.findByTestId("preflight-findings");
    expect(findings.textContent).toContain("un rol que el equipo no tiene");
    expect(findings.textContent).toContain("t2, t7");
    expect(screen.getByTestId("preflight-blockers").textContent).toContain("1");
  });

  it("says explicitly that a clean plan is clean", async () => {
    // El silencio se lee como «no se ha comprobado». Decirlo es la mitad del
    // valor de un semáforo.
    apiFetchMock.mockResolvedValueOnce(CLEAN);
    renderSection();
    expect((await screen.findByTestId("preflight-clean")).textContent).toContain("4 tareas");
  });

  it("shows the shape of the plan even when there is nothing wrong", async () => {
    apiFetchMock.mockResolvedValueOnce(CLEAN);
    renderSection();
    const card = await screen.findByTestId("plan-preflight");
    expect(card.textContent).toContain("2 de 4 tareas en serie");
    expect(card.textContent).toContain("Paralelismo máximo: 3");
  });

  it("does not even ask on an approved plan", () => {
    renderSection("approved");
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("plan-preflight")).toBeNull();
  });

  it("stays silent when the preflight cannot be computed", async () => {
    // Es una ayuda: si falla, aprobar tiene que seguir siendo posible sin un
    // cartel rojo que sugiera que el plan está mal.
    apiFetchMock.mockRejectedValueOnce(new Error("500"));
    renderSection();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("plan-preflight")).toBeNull();
  });
});
