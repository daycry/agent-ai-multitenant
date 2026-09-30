// @vitest-environment jsdom
/**
 * task_mem_20 — la ficha de una memoria: procedencia resuelta (agente, proyecto, título de la
 * ejecución), contadores de uso y utilidad, línea de tiempo y el voto humano (`human_mem_02`).
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MemoryCardView } from "@/components/memories/memory-card-view";
import { ApiError } from "@/lib/api";
import { LanguageProvider } from "@/lib/lang-context";

const ID = "aaaaaaaa-0000-0000-0000-000000000001";
const EXEC = "eeeeeeee-0000-0000-0000-000000000001";
const NEIGHBOUR = "bbbbbbbb-0000-0000-0000-000000000002";

const hit = (id: string, headline: string, execution_id: string) => ({
  id,
  headline,
  type: "semantic",
  kind: "decision",
  scope: "project_shared",
  created_at: "2026-09-01T10:00:00Z",
  agent: "Agente Ficha",
  project: "Proyecto Uno",
  execution_id,
});

const CARD = {
  id: ID,
  tenant_id: "t1",
  scope: "project_shared",
  type: "semantic",
  category: "semantic",
  kind: "decision",
  content: "El proyecto usa asyncpg.",
  tags: [],
  user_id: null,
  team_id: null,
  project_id: "p1",
  source_execution_id: EXEC,
  execution_id: EXEC,
  agent_id: "ag1",
  project_name: "Proyecto Uno",
  agent_name: "Agente Ficha",
  execution_title: "Migrar a asyncpg",
  created_by: { kind: "agent", id: "ag1", name: "Agente Ficha" },
  recall_count: 3,
  last_recalled_at: "2026-09-29T10:00:00Z",
  useful_count: 2,
  harmful_count: 1,
  disputed: false,
  disputed_at: null,
  my_verdict: null as string | null,
  has_embedding: true,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
  timeline: {
    anchor_memory_id: ID,
    anchor_execution_id: EXEC,
    window: 5,
    executions: [
      {
        execution_id: "eeeeeeee-0000-0000-0000-000000000000",
        position: -1,
        started_at: "2026-08-31T10:00:00Z",
        memories: [
          hit(NEIGHBOUR, "Antes: se eligió pgvector", "eeeeeeee-0000-0000-0000-000000000000"),
        ],
      },
      {
        execution_id: EXEC,
        position: 0,
        started_at: "2026-09-01T10:00:00Z",
        memories: [hit(ID, "El proyecto usa asyncpg.", EXEC)],
      },
    ],
  },
};

function mount(card: Record<string, unknown> = CARD) {
  apiFetchMock.mockImplementation((path: string, init?: { method?: string }) => {
    if (init?.method === "POST") {
      return Promise.resolve({
        memory_id: ID,
        verdict: "wrong",
        useful_count: 2,
        harmful_count: 2,
        disputed: true,
      });
    }
    return Promise.resolve(path === `/memories/${ID}` ? card : []);
  });
  return renderCard();
}

function renderCard() {
  window.localStorage.setItem("admin-panel.lang", "es");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <MemoryCardView id={ID} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("ficha de la memoria", () => {
  it("pinta la procedencia resuelta: agente, proyecto y título de la ejecución", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId(`memory-card-${ID}`)).toBeTruthy());
    expect(screen.getByTestId(`memory-who-${ID}`).textContent).toContain("Agente Ficha");
    expect(screen.getByTestId(`memory-where-${ID}`).textContent).toContain("Proyecto Uno");
    expect(screen.getByTestId(`memory-execution-${ID}`).textContent).toContain("Migrar a asyncpg");
  });

  it("enseña los contadores de uso y de utilidad", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("memory-usage-recalls")).toBeTruthy());
    expect(screen.getByTestId("memory-usage-recalls").textContent).toContain("3");
    expect(screen.getByTestId("memory-usage-useful").textContent).toContain("2");
    expect(screen.getByTestId("memory-usage-harmful").textContent).toContain("1");
    expect(screen.queryByTestId("memory-disputed-badge")).toBeNull();
  });

  it("una memoria sin recuperaciones dice «nunca», y una disputada lo señala", async () => {
    mount({
      ...CARD,
      recall_count: 0,
      last_recalled_at: null,
      disputed: true,
      disputed_at: "2026-09-30T10:00:00Z",
    });
    await waitFor(() => expect(screen.getByTestId("memory-usage-recalls")).toBeTruthy());
    expect(screen.getByTestId("memory-usage-recalls").textContent).toContain("nunca");
    expect(screen.getByTestId("memory-disputed-badge")).toBeTruthy();
  });

  it("la línea de tiempo lista las ejecuciones vecinas con enlace a cada memoria", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("memory-timeline")).toBeTruthy());
    const neighbour = screen.getByTestId(`memory-timeline-item-${NEIGHBOUR}`);
    expect(neighbour.textContent).toContain("Antes: se eligió pgvector");
    expect(neighbour.querySelector("a")?.getAttribute("href")).toBe(`/admin/memories/${NEIGHBOUR}`);
  });

  it("marcar como errónea envía el voto a POST /memories/{id}/feedback", async () => {
    mount();
    fireEvent.click(await screen.findByTestId(`memory-feedback-wrong-${ID}`));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        `/memories/${ID}/feedback`,
        expect.objectContaining({ method: "POST", body: { verdict: "wrong" } }),
      ),
    );
  });

  it("el voto ya dado sale pulsado", async () => {
    mount({ ...CARD, my_verdict: "outdated" });
    const outdated = await screen.findByTestId(`memory-feedback-outdated-${ID}`);
    expect(outdated.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId(`memory-feedback-wrong-${ID}`).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("una memoria inexistente o ajena dice que no es visible", async () => {
    apiFetchMock.mockRejectedValue(new ApiError(404, "memory not found"));
    renderCard();
    await waitFor(() => expect(screen.getByTestId("memory-card-error")).toBeTruthy());
    expect(screen.getByTestId("memory-card-error").textContent).toContain("no es visible");
  });
});
