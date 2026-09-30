// @vitest-environment jsdom
/**
 * task_mem_20 — la cola de compactación (`task_mem_11`) pintada para el `tenant_admin`:
 * propuestas pendientes con sus memorias, confirmar y rechazar.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { CompactionQueue } from "@/components/memories/compaction-queue";
import { LanguageProvider } from "@/lib/lang-context";

const M1 = "11111111-0000-0000-0000-000000000001";
const M2 = "22222222-0000-0000-0000-000000000002";
const PROPOSAL = {
  id: "cccccccc-0000-0000-0000-000000000001",
  project_id: "p1",
  project_name: "Proyecto Uno",
  status: "pending",
  memory_ids: [M1, M2],
  summary: "2 memorias disputadas del proyecto",
  created_at: "2026-09-30T04:10:00Z",
  decided_at: null,
};

function mount(proposals: unknown[] = [PROPOSAL]) {
  apiFetchMock.mockImplementation((path: string, init?: { method?: string }) => {
    if (init?.method === "POST") return Promise.resolve({ id: PROPOSAL.id, status: "confirmed" });
    if (path.startsWith("/memories/compactions")) return Promise.resolve(proposals);
    if (path === `/memories/${M1}`) return Promise.resolve({ id: M1, content: "falsa uno" });
    if (path === `/memories/${M2}`) return Promise.resolve({ id: M2, content: "falsa dos" });
    return Promise.resolve([]);
  });
  window.localStorage.setItem("admin-panel.lang", "es");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <CompactionQueue />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("cola de compactación", () => {
  it("lista la propuesta con su proyecto y sus memorias", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId(`compaction-${PROPOSAL.id}`)).toBeTruthy());
    expect(apiFetchMock).toHaveBeenCalledWith("/memories/compactions?status=pending");
    const card = screen.getByTestId(`compaction-${PROPOSAL.id}`);
    expect(card.textContent).toContain("Proyecto Uno");
    await waitFor(() => expect(card.textContent).toContain("falsa uno"));
    expect(card.textContent).toContain("falsa dos");
  });

  it("confirmar y rechazar llaman a sus endpoints", async () => {
    mount();
    fireEvent.click(await screen.findByTestId(`compaction-confirm-${PROPOSAL.id}`));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        `/memories/compactions/${PROPOSAL.id}/confirm`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fireEvent.click(await screen.findByTestId(`compaction-reject-${PROPOSAL.id}`));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        `/memories/compactions/${PROPOSAL.id}/reject`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("sin propuestas pendientes lo dice", async () => {
    mount([]);
    await waitFor(() => expect(screen.getByTestId("compactions-empty")).toBeTruthy());
  });
});
