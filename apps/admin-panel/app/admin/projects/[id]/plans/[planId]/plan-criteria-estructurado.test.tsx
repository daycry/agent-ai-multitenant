// @vitest-environment jsdom
/**
 * IMPORTANTE 4 de la ola 2 del ADR 0162 — el OTRO consumidor tipado `string[]`.
 *
 * La ola 1 hizo que `_clean_acceptance_criteria` conservara la forma
 * estructurada de un criterio (el par `runtime` + `command`, o un `check_type`
 * declarado). Se auditaron los tres consumidores de Python y ninguno de los de
 * TypeScript: `PlanTaskSpec.acceptance_criteria` seguía declarado `string[]`,
 * y de ahí colgaban dos pantallas.
 *
 * Ésta es la peor de las dos, porque no degrada: **revienta**. La lista de
 * correcciones del rechazo (ADR 0107) pintaba cada criterio como hijo de React
 * directamente (`<li>{c}</li>`), y React lanza «Objects are not valid as a React
 * child» ante un diccionario — se lleva por delante la tarjeta entera, así que
 * el operador no puede ni revisar ni aceptar las correcciones de un plan
 * rechazado cuyo planner declaró un criterio ejecutable.
 *
 * Se rinde la PÁGINA, no el helper: el defecto es de renderizado y sólo existe
 * cuando el criterio llega a JSX.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

/** Lo que el planner deja hoy en el spec cuando alguien declara la ejecución. */
const STRUCTURED_CRITERION = {
  description: "La portada responde 200 y sus tests pasan",
  check_type: "automated",
  runtime: "php-phpunit",
  command: "vendor/bin/phpunit --testsuite Feature",
  expected_signal: "exit_code == 0 and tests > 0",
};

const PLAN = {
  id: "plan-1",
  title: "Plan CI4",
  description: null,
  status: "rejected",
  conversation_id: null,
  specification: {
    tasks: [
      {
        id: "fix-1",
        title: "Acotar filtro Content-Type",
        complexity: "s",
        origin: "correction",
        // Uno estructurado y uno en prosa: la mezcla es el caso real, porque la
        // inmensa mayoría de criterios sigue siendo prosa y sólo declara quien
        // acaba de escribir el test.
        acceptance_criteria: [STRUCTURED_CRITERION, "Y el README lo explica"],
      },
    ],
    corrections: [
      {
        session_id: "sess-1",
        reason: "El filtro es global y rompe la portada.",
        task_ids: ["fix-1"],
        status: "proposed",
      },
    ],
  },
  approved_by: null,
  approved_at: null,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/plans/plan-1") return Promise.resolve(PLAN);
    if (path === "/plans/plan-1/status") {
      return Promise.resolve({
        plan_id: "plan-1",
        status: "rejected",
        progress: { total: 1, done: 0, open: 1, label: "0/1" },
        pr: { url: null, branch: null, error: null },
        cost: {
          ai_currency: "USD",
          human_currency: "EUR",
          estimated_ai_min: "1.00",
          estimated_ai_max: "4.00",
          estimated_human_hours: "8.000",
          estimated_human_cost: "400.00",
          actual_ai_cost: "0",
          actual_tokens: 0,
          actual_runs: 0,
          over_estimate: false,
        },
      });
    }
    if (path === "/plans/plan-1/review-session") return Promise.reject(new Error("404"));
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

describe("Correcciones del rechazo — un criterio estructurado se PINTA", () => {
  it("enseña su texto y no tumba la tarjeta", async () => {
    wireApi();
    mount();

    // La tarjeta sigue en pie: es la condición previa: si React tumba el árbol,
    // aquí ya no hay nada que buscar.
    await waitFor(() => expect(screen.getByTestId("plan-corrections")).toBeTruthy());

    const list = await screen.findByTestId("plan-correction-task-fix-1");
    expect(list.textContent).toContain("La portada responde 200 y sus tests pasan");
    expect(list.textContent).toContain("Y el README lo explica");
    // Y no se cuela el `repr` del diccionario, que es lo que ve el operador
    // cuando alguien «arregla» esto con un `String(c)`.
    expect(list.textContent).not.toContain("[object Object]");
  });
});
