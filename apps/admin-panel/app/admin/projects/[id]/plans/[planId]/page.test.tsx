// @vitest-environment jsdom
// ADR 0107 — tarjeta «Correcciones del rechazo» en el detalle del plan:
//   - un plan `rejected` muestra el motivo del validador y el botón Generar
//     (POST /plans/{id}/generate-corrections);
//   - con correcciones `proposed` en el spec, pinta las tareas con checkboxes
//     y Aceptar envía SOLO los ids marcados a accept-corrections;
//   - fuera de `rejected` y sin correcciones, la tarjeta no existe;
//   - las tareas origin=correction llevan badge en la tabla de tareas.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1", planId: "plan-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/plans/plan-1",
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import PlanDetailPage from "@/app/admin/projects/[id]/plans/[planId]/page";

const REASON = "El filtro JSON es **global** y rompe la portada HTML.";

function plan(overrides: Record<string, unknown> = {}) {
  return {
    id: "plan-1",
    title: "Plan CI4",
    description: null,
    status: "rejected",
    conversation_id: null,
    specification: { tasks: [{ id: "t1", title: "Original", complexity: "m" }] },
    approved_by: null,
    approved_at: null,
    created_at: "2026-07-08T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    ...overrides,
  };
}

const SPEC_WITH_PROPOSED = {
  tasks: [
    { id: "t1", title: "Original", complexity: "m" },
    {
      id: "fix-1",
      title: "Acotar filtro Content-Type",
      complexity: "s",
      origin: "correction",
      acceptance_criteria: ["La portada responde text/html"],
    },
    {
      id: "fix-2",
      title: "Test de regresión",
      complexity: "s",
      origin: "correction",
      depends_on: ["fix-1"],
    },
  ],
  corrections: [
    {
      session_id: "sess-1",
      reason: REASON,
      task_ids: ["fix-1", "fix-2"],
      status: "proposed",
    },
  ],
};

function wireApi(planBody: Record<string, unknown>, session: Record<string, unknown> | null) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (opts?.method === "POST" && path === "/plans/plan-1/generate-corrections") {
      return Promise.resolve({
        session_id: "sess-1",
        reason: REASON,
        task_ids: ["fix-1", "fix-2"],
        tasks: [],
        already_generated: false,
      });
    }
    if (opts?.method === "POST" && path === "/plans/plan-1/accept-corrections") {
      return Promise.resolve(planBody);
    }
    if (path === "/plans/plan-1") return Promise.resolve(planBody);
    // task_wf_30: la cabecera de estado (progreso + PR + coste real).
    if (path === "/plans/plan-1/status") {
      return Promise.resolve({
        plan_id: "plan-1",
        status: planBody.status,
        progress: { total: 2, done: 1, open: 1, label: "1/2" },
        pr: { url: null, branch: null, error: null },
        cost: {
          ai_currency: "USD",
          human_currency: "EUR",
          estimated_ai_min: "1.00",
          estimated_ai_max: "4.00",
          estimated_human_hours: "16.000",
          estimated_human_cost: "800.00",
          actual_ai_cost: "0",
          actual_tokens: 0,
          actual_runs: 0,
          over_estimate: false,
        },
      });
    }
    if (path === "/plans/plan-1/review-session") {
      return session ? Promise.resolve(session) : Promise.reject(new Error("404"));
    }
    if (path.includes("/comments")) return Promise.resolve([]);
    if (path.includes("/cost-breakdown")) return Promise.reject(new Error("404"));
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PlanDetailPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Correcciones del rechazo (ADR 0107)", () => {
  it("shows the rejection reason and generates corrections on click", async () => {
    wireApi(plan(), {
      session_id: "sess-1",
      status: "rejected",
      verdict: "rejected",
      rejection_reason: REASON,
      expires_at: null,
      review_url: "http://x/review",
      app_url: "http://x/app",
      verdict_url: "http://x/verdict",
    });
    mount();

    await waitFor(() => expect(screen.getByTestId("plan-corrections")).toBeTruthy());
    const reasonBox = await screen.findByTestId("plan-corrections-reason");
    expect(reasonBox.textContent).toContain("rompe la portada HTML");

    fireEvent.click(screen.getByTestId("plan-corrections-generate"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/plans/plan-1/generate-corrections", {
        method: "POST",
      }),
    );
  });

  it("accepts only the checked corrective tasks", async () => {
    wireApi(plan({ specification: SPEC_WITH_PROPOSED }), null);
    mount();

    await waitFor(() => expect(screen.getByTestId("plan-correction-task-fix-1")).toBeTruthy());
    // La tarjeta pinta título + criterios de la propuesta.
    expect(screen.getByTestId("plan-correction-task-fix-1").textContent).toContain(
      "Acotar filtro Content-Type",
    );
    expect(screen.getByTestId("plan-correction-task-fix-1").textContent).toContain(
      "La portada responde text/html",
    );

    // Desmarcar fix-2 → solo fix-1 viaja en el accept.
    fireEvent.click(screen.getByTestId("plan-correction-check-fix-2"));
    fireEvent.click(screen.getByTestId("plan-corrections-accept"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/plans/plan-1/accept-corrections", {
        method: "POST",
        body: { task_ids: ["fix-1"] },
      }),
    );
  });

  it("marks correction tasks with a badge in the tasks table", async () => {
    wireApi(plan({ specification: SPEC_WITH_PROPOSED }), null);
    mount();
    await waitFor(() => expect(screen.getByTestId("plan-task-origin-fix-1")).toBeTruthy());
    expect(screen.getByTestId("plan-task-origin-fix-1").textContent).toBe("corrección");
    expect(screen.queryByTestId("plan-task-origin-t1")).toBeNull();
  });

  it("renders nothing outside rejected when there are no corrections", async () => {
    wireApi(plan({ status: "in_progress" }), null);
    mount();
    await waitFor(() => expect(screen.getByTestId("plan-detail")).toBeTruthy());
    expect(screen.queryByTestId("plan-corrections")).toBeNull();
  });
});
