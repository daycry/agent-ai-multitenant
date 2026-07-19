// @vitest-environment jsdom
// Bandeja del humano (ADR 0123): GET /human-queue renderizado por antigüedad
// con la insignia de tipo y el enlace a la pantalla donde se resuelve.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }));

import HumanQueuePage from "@/app/admin/human-queue/page";

const ITEMS = [
  {
    kind: "plan_validation",
    id: "p1",
    title: "Plan esperando",
    project_name: "Demo",
    age_seconds: 30 * 3600,
    url_path: "/admin/plans/p1",
  },
  {
    kind: "run_review",
    id: "r1",
    title: "Tarea escalada",
    project_name: "Demo",
    age_seconds: 600,
    url_path: "/admin/executions/r1",
  },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <HumanQueuePage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("HumanQueuePage", () => {
  it("pinta los items por antigüedad con edad legible y enlace", async () => {
    apiFetchMock.mockResolvedValueOnce(ITEMS);
    renderPage();
    const first = await screen.findByTestId("hq-item-0");
    expect(first.textContent).toContain("Plan esperando");
    expect(first.textContent).toContain("1 d"); // 30 h → días
    const second = screen.getByTestId("hq-item-1");
    expect(second.textContent).toContain("10 min");
  });

  it("cola vacía = mensaje de paz", async () => {
    apiFetchMock.mockResolvedValueOnce([]);
    renderPage();
    expect(await screen.findByTestId("hq-empty")).toBeTruthy();
  });
});
