// @vitest-environment jsdom
// Caracterización del catálogo Modelos & Precios (tramo #9, auditoría 2026-07-10):
// red de tests ANTES de modularizar el monolito de 1311 líneas. Clava:
//   - la tabla del catálogo (una fila por precio, formato USD, badge vigente,
//     acciones de System Admin y el aviso de scope del sync);
//   - el histórico por modelo (dialog + sparkline SVG + filas con vigencia);
//   - el dry-run del sync: subida >10% exige el checkbox antes de aplicar y el
//     apply envía {confirm:true};
//   - el form de crear con la nota USD-canónico y su cierre.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isTenantAdmin: false,
    isTenantMember: false,
    isLoading: false,
  }),
}));

import ModelPricesPage from "@/app/admin/model-prices/page";

function price(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    provider: "anthropic",
    model_id: "claude-sonnet-4-5",
    modality: "text",
    input_price: "3.0000000000",
    output_price: "15.0000000000",
    cached_input_price: "0.3000000000",
    unit: "per_1m_tokens",
    currency: "USD",
    context_window: 200000,
    source: "manual",
    provider_id: null,
    effective_from: "2026-06-01T00:00:00Z",
    effective_to: null,
    updated_by: null,
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    ...overrides,
  };
}

const PROVIDERS = [{ id: "prov-1", kind: "claude_sdk", display_name: "Claude", is_active: true }];

function wireApi({
  prices = [price(), price({ id: "p2", effective_to: "2026-07-01T00:00:00Z" })],
  providers = PROVIDERS,
  diff = null as Record<string, unknown> | null,
}: {
  prices?: Record<string, unknown>[];
  providers?: Record<string, unknown>[];
  diff?: Record<string, unknown> | null;
} = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/admin/llm-providers") return Promise.resolve(providers);
    if (path === "/admin/model-prices/sync/diff" && opts?.method === "POST") {
      return diff ? Promise.resolve(diff) : Promise.reject(new Error("sin diff cableado"));
    }
    if (path === "/admin/model-prices/sync/apply" && opts?.method === "POST") {
      return Promise.resolve({ fetched: 1, created: 1, updated: 0, unchanged: 0, changed: 1 });
    }
    if (path.startsWith("/model-prices?")) {
      // limit=200 → el histórico del dialog; el resto, la lista principal.
      return Promise.resolve(path.includes("limit=200") ? prices : prices);
    }
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ModelPricesPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Modelos & Precios (caracterización tramo #9)", () => {
  it("renders one row per price with USD formatting, current badge and admin actions", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("prices-table")).toBeTruthy());
    expect(screen.getByTestId("price-row-p1")).toBeTruthy();
    expect(screen.getByTestId("price-row-p2")).toBeTruthy();
    // fmtUsd: "3.0000000000" → "$3" (6 decimales significativos, ceros fuera).
    expect(screen.getByTestId("price-input-p1").textContent).toContain("$3");
    // Solo la fila vigente (effective_to=null) lleva el badge.
    expect(screen.getByTestId("price-current-p1")).toBeTruthy();
    expect(screen.queryByTestId("price-current-p2")).toBeNull();
    // Acciones de System Admin + scope del sync derivado del provider activo.
    expect(screen.getByTestId("price-create-open")).toBeTruthy();
    expect(screen.getByTestId("price-sync-open")).toBeTruthy();
    expect(screen.getByTestId("sync-scope-families").textContent).toContain("anthropic");
  });

  it("shows the empty-scope warning when no provider is active", async () => {
    wireApi({ providers: [{ ...PROVIDERS[0], is_active: false }] });
    mount();
    await waitFor(() => expect(screen.getByTestId("sync-scope-empty")).toBeTruthy());
  });

  it("opens the history dialog with sparkline and effective-dated rows", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("price-history-p1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("price-history-p1"));
    await waitFor(() => expect(screen.getByTestId("price-history-dialog")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("history-table")).toBeTruthy());
    expect(screen.getByTestId("price-chart")).toBeTruthy();
    expect(screen.getByTestId("history-row-p1")).toBeTruthy();
    expect(screen.getByTestId("history-row-p2")).toBeTruthy();
    // El fetch del histórico fija la clave (provider+model_id+modality) y va
    // SIN current_only (trae también los periodos cerrados).
    const historyCall = apiFetchMock.mock.calls.find(
      ([p]) => typeof p === "string" && p.includes("model_id="),
    );
    expect(historyCall?.[0]).toContain("provider=anthropic");
    expect(historyCall?.[0]).not.toContain("current_only");
  });

  it("gates the sync apply behind the >10% confirmation checkbox", async () => {
    wireApi({
      diff: {
        fetched: 3,
        added: 1,
        updated: 0,
        unchanged: 1,
        increased: 1,
        removed: 0,
        has_large_increase: true,
        rows: [
          {
            provider: "anthropic",
            model_id: "claude-sonnet-4-5",
            modality: "text",
            status: "increased",
            source: "litellm",
            old_input: "3",
            new_input: "3.6",
            old_output: "15",
            new_output: "15",
            old_cached_input: null,
            new_cached_input: null,
            input_pct: 0.2,
            output_pct: 0,
            manual_skipped: false,
          },
        ],
        skipped: [],
      },
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("price-sync-open")).toBeTruthy());
    fireEvent.click(screen.getByTestId("price-sync-open"));
    await waitFor(() => expect(screen.getByTestId("sync-summary")).toBeTruthy());
    expect(screen.getByTestId("sync-confirm-gate")).toBeTruthy();
    const apply = screen.getByTestId("sync-apply") as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
    fireEvent.click(screen.getByTestId("sync-confirm-checkbox"));
    await waitFor(() =>
      expect((screen.getByTestId("sync-apply") as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("sync-apply"));
    await waitFor(() => {
      const applyCall = apiFetchMock.mock.calls.find(
        ([p]) => p === "/admin/model-prices/sync/apply",
      );
      expect(applyCall?.[1]?.body).toEqual({ confirm: true });
    });
  });

  it("opens the create form with the USD note and closes on cancel", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("price-create-open")).toBeTruthy());
    fireEvent.click(screen.getByTestId("price-create-open"));
    await waitFor(() => expect(screen.getByTestId("price-form-dialog")).toBeTruthy());
    expect(screen.getByTestId("price-form-usd-note")).toBeTruthy();
    fireEvent.click(screen.getByTestId("price-form-cancel"));
    await waitFor(() => expect(screen.queryByTestId("price-form-dialog")).toBeNull());
  });
});
