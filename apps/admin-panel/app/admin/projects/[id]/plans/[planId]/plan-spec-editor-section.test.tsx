// @vitest-environment jsdom
// `task_wf_42`: el editor del spec antes de aprobar.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ApiError } from "@/lib/api";
import { PlanSpecEditorSection } from "@/app/admin/projects/[id]/plans/[planId]/plan-spec-editor-section";
import type { PlanSpecification } from "@/app/admin/projects/[id]/plans/[planId]/plan-spec-types";

const SPEC: PlanSpecification = {
  summary: { title: "Migración", description: "Mover el esquema" },
  phases: [{ title: "Fase 1", tasks: ["t1"] }],
  estimates: { effort_person_days: 3 },
  tasks: [
    { id: "t1", title: "Migrar el esquema", depends_on: [] },
    { id: "t2", title: "Cargar los datos", depends_on: ["t1"] },
  ],
};

function renderSection(status = "pending_approval") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PlanSpecEditorSection planId="p-1" status={status} spec={SPEC} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PlanSpecEditorSection", () => {
  it("does not offer the editor once the plan has been signed", () => {
    renderSection("approved");
    expect(screen.queryByTestId("plan-spec-edit-open")).toBeNull();
    // La tabla de siempre sigue ahí: la sección no desaparece, solo el botón.
    expect(screen.getByTestId("plan-tasks")).toBeTruthy();
  });

  it("saves the WHOLE spec, not just the tasks", async () => {
    // `PlanSpecification` tiene defaults por campo en el backend: un PUT solo
    // con `tasks` borraría summary, phases y estimates sin decir nada.
    apiFetchMock.mockResolvedValueOnce({ id: "p-1", specification: SPEC });
    renderSection();

    fireEvent.click(screen.getByTestId("plan-spec-edit-open"));
    fireEvent.change(screen.getByTestId("plan-spec-title-0"), {
      target: { value: "Migrar el esquema (v2)" },
    });
    fireEvent.click(screen.getByTestId("plan-spec-save"));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    const [path, options] = apiFetchMock.mock.calls[0] as [
      string,
      { body: { specification: PlanSpecification } },
    ];
    expect(path).toBe("/plans/p-1");
    const sent = options.body.specification;
    expect(sent.summary).toEqual(SPEC.summary);
    expect(sent.phases).toEqual(SPEC.phases);
    expect(sent.estimates).toEqual(SPEC.estimates);
    expect(sent.tasks?.[0].title).toBe("Migrar el esquema (v2)");
  });

  it("prunes the dependency when its task is removed", async () => {
    apiFetchMock.mockResolvedValueOnce({ id: "p-1", specification: SPEC });
    renderSection();

    fireEvent.click(screen.getByTestId("plan-spec-edit-open"));
    fireEvent.click(screen.getByTestId("plan-spec-remove-0")); // quita t1
    fireEvent.click(screen.getByTestId("plan-spec-save"));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    const [, options] = apiFetchMock.mock.calls[0] as [
      string,
      { body: { specification: PlanSpecification } },
    ];
    expect(options.body.specification.tasks).toEqual([{ id: "t2", title: "Cargar los datos" }]);
  });

  it("explains a DAG cycle instead of dumping the JSON", async () => {
    apiFetchMock.mockRejectedValueOnce(
      new ApiError(
        422,
        JSON.stringify({ detail: { error: "dag_cycle", cycle: ["t1", "t2", "t1"] } }),
      ),
    );
    renderSection();

    fireEvent.click(screen.getByTestId("plan-spec-edit-open"));
    fireEvent.click(screen.getByTestId(`plan-spec-dep-0-t2`)); // t1 pasa a depender de t2
    fireEvent.click(screen.getByTestId("plan-spec-save"));

    const error = await screen.findByTestId("plan-spec-save-error");
    expect(error.textContent).toContain("Migrar el esquema");
    expect(error.textContent).not.toContain("{");
    // Y el editor sigue abierto con lo escrito: cerrarlo obligaría a repetirlo.
    expect(screen.getByTestId("plan-spec-editor")).toBeTruthy();
  });

  it("refuses to send a task without a title", () => {
    renderSection();
    fireEvent.click(screen.getByTestId("plan-spec-edit-open"));
    fireEvent.change(screen.getByTestId("plan-spec-title-1"), { target: { value: "  " } });

    expect(screen.getByTestId("plan-spec-problems").textContent).toContain("no tiene título");
    expect((screen.getByTestId("plan-spec-save") as HTMLButtonElement).disabled).toBe(true);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("adds a task with a free id", () => {
    renderSection();
    fireEvent.click(screen.getByTestId("plan-spec-edit-open"));
    fireEvent.click(screen.getByTestId("plan-spec-add-task"));
    expect((screen.getByTestId("plan-spec-id-2") as HTMLInputElement).value).toBe("t3");
  });
});
