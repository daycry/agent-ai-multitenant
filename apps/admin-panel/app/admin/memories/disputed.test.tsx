// @vitest-environment jsdom
/**
 * task_mem_20 — `/admin/memories`: pestaña «Disputadas», filtro «nunca recuperadas» y la cola de
 * compactación, que sólo ve el `tenant_admin`.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import MemoriesPage from "@/app/admin/memories/page";
import { LanguageProvider } from "@/lib/lang-context";

const ROW = {
  id: "m-1",
  tenant_id: "t1",
  scope: "global",
  type: "semantic",
  kind: null,
  content: "memoria de prueba",
  tags: [],
  user_id: null,
  team_id: null,
  project_id: null,
  source_execution_id: null,
  agent_id: null,
  has_embedding: false,
  created_at: "2026-08-01T10:00:00Z",
  updated_at: "2026-08-01T10:00:00Z",
  recall_count: 0,
  useful_count: 0,
  harmful_count: 1,
  disputed: true,
  created_by: { kind: "unknown", id: null, name: null },
};
const COUNTS = { all: 1, private: 0, team_shared: 0, project_shared: 0, global: 1 };
const ME = (role: string) => ({
  user_id: "u1",
  email: "a@b.c",
  full_name: "A",
  is_system_admin: false,
  active_tenant_id: "t1",
  memberships: [{ tenant_id: "t1", tenant_name: "T", role, is_active: true }],
});

function mount(role = "tenant_user") {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/me") return Promise.resolve(ME(role));
    if (path === "/memories/counts") return Promise.resolve(COUNTS);
    if (path.startsWith("/memories/compactions")) return Promise.resolve([]);
    if (path.startsWith("/memories")) return Promise.resolve([ROW]);
    return Promise.resolve([]);
  });
  window.localStorage.setItem("admin-panel.lang", "es");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <MemoriesPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

const calls = () => apiFetchMock.mock.calls.map((c) => String(c[0]));

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("/admin/memories — disputadas, nunca recuperadas y compactación", () => {
  it("la pestaña Disputadas pide disputed=true", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("memory-m-1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("memories-scope-disputed"));
    await waitFor(() => expect(calls()).toContain("/memories?disputed=true"));
  });

  it("«nunca recuperadas» pide max_recall_count=0", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("memory-m-1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("memories-never-recalled"));
    await waitFor(() => expect(calls()).toContain("/memories?max_recall_count=0"));
  });

  it("cada fila avisa de que está disputada y enlaza a su ficha", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("memory-disputed-m-1")).toBeTruthy());
    expect(screen.getByTestId("memory-open-m-1").getAttribute("href")).toBe("/admin/memories/m-1");
    expect(screen.getByTestId("memory-feedback-wrong-m-1")).toBeTruthy();
  });

  it("la cola de compactación sólo se pide y se pinta para tenant_admin", async () => {
    mount("tenant_user");
    await waitFor(() => expect(screen.getByTestId("memory-m-1")).toBeTruthy());
    await waitFor(() => expect(calls()).toContain("/me"));
    expect(calls().some((p) => p.startsWith("/memories/compactions"))).toBe(false);
    expect(screen.queryByTestId("compactions-card")).toBeNull();
    cleanup();
    apiFetchMock.mockReset();
    mount("tenant_admin");
    await waitFor(() => expect(screen.getByTestId("compactions-card")).toBeTruthy());
    expect(calls().some((p) => p.startsWith("/memories/compactions"))).toBe(true);
  });
});
