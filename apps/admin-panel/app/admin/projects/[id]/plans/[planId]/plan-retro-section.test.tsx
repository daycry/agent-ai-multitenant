// @vitest-environment jsdom
// `task_wf_34`: la retro del plan, que hasta ahora se escribía para nadie.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ApiError } from "@/lib/api";
import {
  planHasRetro,
  PlanRetroSection,
} from "@/app/admin/projects/[id]/plans/[planId]/plan-retro-section";

function renderSection(status: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PlanRetroSection planId="p-1" status={status} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("planHasRetro", () => {
  it("only asks for a plan the beat would have processed", () => {
    // El beat solo mira `completed`/`cancelled`: pedirla antes es una llamada
    // que siempre da 404.
    expect(planHasRetro("completed")).toBe(true);
    expect(planHasRetro("cancelled")).toBe(true);
    for (const status of ["draft", "pending_approval", "approved", "in_progress", "blocked"]) {
      expect(planHasRetro(status)).toBe(false);
    }
  });
});

describe("PlanRetroSection", () => {
  it("renders the retro of a closed plan", async () => {
    apiFetchMock.mockResolvedValueOnce({
      plan_id: "p-1",
      memory_id: "m-1",
      content: "Retrospectiva del plan «Migración» (completed)\n- Tareas: 4/5 hechas",
      created_at: "2026-07-26T10:00:00Z",
    });
    renderSection("completed");
    expect((await screen.findByTestId("plan-retro-content")).textContent).toContain("4/5 hechas");
  });

  it("stays silent when there is no attributable retro", async () => {
    // Un plan cerrado antes del etiquetado no tiene retro atribuible. Un cartel
    // de error sería ruido por algo que el operador no puede arreglar.
    apiFetchMock.mockRejectedValueOnce(new ApiError(404, "no retro for this plan yet"));
    renderSection("completed");
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("plan-retro")).toBeNull();
  });

  it("does not even ask for a plan that is still running", () => {
    renderSection("in_progress");
    expect(apiFetchMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId("plan-retro")).toBeNull();
  });
});
