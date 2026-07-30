// @vitest-environment jsdom
// `task_wf_40`: las cinco acciones humanas, ahora compartidas entre el panel de
// escaladas y la ficha de la tarea.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { acceptsHumanAction, TaskHumanActions } from "@/components/tasks/task-human-actions";

function renderActions(onApplied = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TaskHumanActions taskId="t-1" onApplied={onApplied} />
    </QueryClientProvider>,
  );
  return onApplied;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("acceptsHumanAction", () => {
  it("mirrors the backend gate exactly", () => {
    // `task_lifecycle.py` responde 409 desde cualquier otro estado. Si esta
    // lista se ensancha, la UI ofrece un botón que siempre falla; si se
    // estrecha, una tarea parada se queda sin salida.
    expect(acceptsHumanAction("awaiting_human_approval")).toBe(true);
    expect(acceptsHumanAction("blocked")).toBe(true);
    for (const status of ["backlog", "ready", "running", "done", "cancelled", "in_review"]) {
      expect(acceptsHumanAction(status)).toBe(false);
    }
  });

  it("treats a missing status as not actionable", () => {
    // Mientras el detalle carga no hay estado: ofrecer las acciones ahí sería
    // apostar a que la tarea está parada.
    expect(acceptsHumanAction(undefined)).toBe(false);
    expect(acceptsHumanAction(null)).toBe(false);
  });
});

describe("TaskHumanActions", () => {
  it("posts the action and notifies the caller so it can refresh", async () => {
    apiFetchMock.mockResolvedValueOnce({ ok: true });
    const onApplied = renderActions();

    fireEvent.click(screen.getByTestId("approve-t-1"));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    expect(apiFetchMock).toHaveBeenCalledWith("/tasks/t-1/human-action", {
      method: "POST",
      body: { action: "approve_manual" },
    });
  });

  it("asks for the reason before blocking, and refuses to send an empty one", async () => {
    apiFetchMock.mockResolvedValueOnce({ ok: true });
    renderActions();

    fireEvent.click(screen.getByTestId("block-t-1"));
    const submit = screen.getByTestId("block-submit") as HTMLButtonElement;
    // Un bloqueo sin motivo deja al siguiente humano sin saber qué esperar: el
    // backend lo acepta, la UI no.
    expect(submit.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("block-reason-edit"), {
      target: { value: "  Esperando credencial del cliente  " },
    });
    fireEvent.click(screen.getByTestId("block-submit"));

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith("/tasks/t-1/human-action", {
        method: "POST",
        body: { action: "block_with_reason", reason: "Esperando credencial del cliente" },
      }),
    );
  });

  it("does not carry the previous text into the next dialog", async () => {
    apiFetchMock.mockResolvedValueOnce({ ok: true });
    renderActions();

    fireEvent.click(screen.getByTestId("reassign-t-1"));
    fireEvent.change(screen.getByTestId("reassign-guidance-edit"), {
      target: { value: "Prueba otro enfoque" },
    });
    fireEvent.click(screen.getByTestId("reassign-submit"));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));

    // Reabrir con la guía anterior escrita invitaría a mandarla dos veces sin
    // leerla, que es peor que empezar en blanco.
    fireEvent.click(screen.getByTestId("reassign-t-1"));
    expect((screen.getByTestId("reassign-guidance-edit") as HTMLTextAreaElement).value).toBe("");
  });

  it("surfaces a rejected action instead of swallowing it", async () => {
    apiFetchMock.mockRejectedValueOnce(new Error("409: task is in 'done'"));
    renderActions();

    fireEvent.click(screen.getByTestId("cancel-t-1"));

    const error = await screen.findByTestId("action-error");
    expect(error.textContent).toContain("409");
  });
});
