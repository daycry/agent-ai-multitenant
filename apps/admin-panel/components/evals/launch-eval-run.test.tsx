// @vitest-environment jsdom
// `task_wf_52b`: el botón que faltaba para PRODUCIR evals. El subsistema
// estaba entero y sus tablas vacías porque nadie podía lanzar una corrida.
//
// Lo que se prueba son las dos trampas que el formulario tiene que impedir sin
// gastar una llamada: el juez igual al sujeto (se auto-aprueba) y el dataset
// vacío (100 % sin haber juzgado nada).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// El botón vive tras `<RoleGuard min="tenant_admin">`: lanzar una corrida es
// gasto y el gasto es del admin. Sin este doble, el guard no renderiza nada.
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import { ApiError } from "@/lib/api";

import { LaunchEvalRun, describeLaunchError } from "@/components/evals/launch-eval-run";

const DATASETS = [
  { id: "d-full", name: "dorado", kind: "golden", item_count: 12 },
  { id: "d-empty", name: "recién creado", kind: "golden", item_count: 0 },
];

function renderLaunch() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <LaunchEvalRun />
    </QueryClientProvider>,
  );
}

async function openDialog() {
  fireEvent.click(screen.getByTestId("launch-eval-run-open"));
  await waitFor(() => expect(screen.getByTestId("launch-eval-run-dataset")).toBeTruthy());
}

function fill(dataset: string, subject: string, judge: string) {
  fireEvent.change(screen.getByTestId("launch-eval-run-dataset"), { target: { value: dataset } });
  fireEvent.change(screen.getByTestId("launch-eval-run-subject"), { target: { value: subject } });
  fireEvent.change(screen.getByTestId("launch-eval-run-judge"), { target: { value: judge } });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LaunchEvalRun", () => {
  it("lanza la corrida con el dataset y los dos modelos elegidos", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/eval-datasets") return Promise.resolve(DATASETS);
      return Promise.resolve({
        id: "run-1",
        status: "completed",
        total_items: 12,
        passed_items: 9,
        pass_rate: "0.750",
      });
    });
    renderLaunch();
    await openDialog();
    await waitFor(() => expect(screen.getByText(/dorado \(12 items\)/)).toBeTruthy());
    fill("d-full", "qwen2.5-coder:14b", "claude-sonnet-5");

    fireEvent.click(screen.getByTestId("launch-eval-run-submit"));
    await waitFor(() => expect(screen.getByTestId("launch-eval-run-done")).toBeTruthy());

    const post = apiFetchMock.mock.calls.find(([p]) => p === "/eval-runs");
    expect(post).toBeTruthy();
    expect((post as [string, { body: Record<string, unknown> }])[1].body).toEqual({
      dataset_id: "d-full",
      subject_model: "qwen2.5-coder:14b",
      judge_model: "claude-sonnet-5",
    });
    expect(screen.getByTestId("launch-eval-run-done").textContent).toContain("9/12");
  });

  it("no deja lanzar si el juez es el mismo modelo que el sujeto", async () => {
    apiFetchMock.mockResolvedValue(DATASETS);
    renderLaunch();
    await openDialog();
    fill("d-full", "mismo-modelo", "mismo-modelo");

    expect(screen.getByTestId("launch-eval-run-same-model")).toBeTruthy();
    expect((screen.getByTestId("launch-eval-run-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("no deja lanzar contra un dataset sin items", async () => {
    apiFetchMock.mockResolvedValue(DATASETS);
    renderLaunch();
    await openDialog();
    await waitFor(() => expect(screen.getByText(/recién creado \(0 items\)/)).toBeTruthy());
    fill("d-empty", "sujeto", "juez");

    // Sin esto la corrida daría un pass_rate del 100 % sin haber juzgado nada:
    // el peor dato posible, porque parece perfecto.
    expect(screen.getByTestId("launch-eval-run-empty")).toBeTruthy();
    expect((screen.getByTestId("launch-eval-run-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("la versión de prompt solo viaja si se rellena", async () => {
    apiFetchMock.mockImplementation((path: string) =>
      path === "/eval-datasets"
        ? Promise.resolve(DATASETS)
        : Promise.resolve({ id: "r", status: "completed", total_items: 1, passed_items: 1 }),
    );
    renderLaunch();
    await openDialog();
    // Sin esperar a que carguen las opciones, un `change` a un value sin
    // <option> deja el select vacío y el submit queda deshabilitado.
    await waitFor(() => expect(screen.getByText(/dorado \(12 items\)/)).toBeTruthy());
    fill("d-full", "s", "j");
    fireEvent.change(screen.getByTestId("launch-eval-run-prompt-version"), {
      target: { value: "abc123" },
    });
    fireEvent.click(screen.getByTestId("launch-eval-run-submit"));

    await waitFor(() => expect(screen.getByTestId("launch-eval-run-done")).toBeTruthy());
    const post = apiFetchMock.mock.calls.find(([p]) => p === "/eval-runs") as [
      string,
      { body: Record<string, unknown> },
    ];
    expect(post[1].body.subject_prompt_version).toBe("abc123");
  });
});

describe("describeLaunchError", () => {
  it("traduce el 409 de juez==sujeto a algo accionable", () => {
    const error = new ApiError(
      409,
      JSON.stringify({ detail: { error: "same_model_judge", message: "x" } }),
    );
    expect(describeLaunchError(error)).toContain("distinto del sujeto");
  });

  it("el dataset vacío dice qué hacer, no solo qué pasó", () => {
    const error = new ApiError(
      422,
      JSON.stringify({ detail: { error: "empty_dataset", message: "x" } }),
    );
    expect(describeLaunchError(error)).toContain("Promover a dataset");
  });

  it("la falta de proveedor apunta a dónde se arregla", () => {
    const error = new ApiError(
      503,
      JSON.stringify({ detail: { error: "no_llm_provider", message: "x" } }),
    );
    expect(describeLaunchError(error)).toContain("Proveedores LLM");
  });

  it("el dataset demasiado grande usa las cifras del backend, no unas inventadas", () => {
    const error = new ApiError(
      422,
      JSON.stringify({
        detail: {
          error: "dataset_too_large",
          message: "esta corrida serían 400 llamadas a modelo y el máximo son 200.",
        },
      }),
    );
    expect(describeLaunchError(error)).toContain("400 llamadas");
  });

  it("un cuerpo que no es JSON se enseña tal cual en vez de reventar el diálogo", () => {
    expect(describeLaunchError(new ApiError(502, "<html>Bad Gateway</html>"))).toContain(
      "Bad Gateway",
    );
  });

  it("un error desconocido cae al mensaje del backend en vez de tragárselo", () => {
    const error = new ApiError(
      500,
      JSON.stringify({ detail: { message: "se rompió por dentro" } }),
    );
    expect(describeLaunchError(error)).toBe("se rompió por dentro");
  });
});
