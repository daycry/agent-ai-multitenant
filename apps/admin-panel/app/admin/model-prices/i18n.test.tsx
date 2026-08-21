// @vitest-environment jsdom

/**
 * `model-prices`, migrada al diccionario y repartida en secciones (plan
 * prod-16, `task_prod16_04` + `task_prod16_06`).
 *
 * Dos deudas a la vez. La estructural: `page.tsx` tenía 514 líneas y
 * `model-price-dialogs.tsx` 686 con tres diálogos dentro. La de idioma: todo el
 * texto estaba cableado en castellano, así que con el toggle en EN se leía
 * "Sincronizar precios", "Familia (provider)" y "Aplicar cambios".
 *
 * `page.test.tsx` cubre el COMPORTAMIENTO (tabla, histórico, gate del sync,
 * formulario) y fue la red de seguridad del troceo. Este fichero cubre lo otro:
 * que las cinco piezas resultantes hablen los dos idiomas y que ninguna se
 * quedara sin montar por el camino.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

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

const STORAGE_KEY = "admin-panel.lang";

const PRICE = {
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
};

const PROVIDERS = [{ id: "prov-1", kind: "claude_sdk", display_name: "Claude", is_active: true }];

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/admin/llm-providers") return Promise.resolve(PROVIDERS);
    if (path.startsWith("/model-prices?")) return Promise.resolve([PRICE]);
    return Promise.resolve([]);
  });
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ModelPricesPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("model-prices en castellano", () => {
  it("rinde cabecera, filtros y tabla", async () => {
    renderIn("es");

    expect(await screen.findByText("Modelos & Precios")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("prices-table")).toBeTruthy());
    expect(screen.getByText("Familia (provider)")).toBeDefined();
    expect(screen.getByText("Solo vigentes")).toBeDefined();
    expect(screen.getByTestId("price-current-p1").textContent).toContain("vigente");
    // La unidad sale de UNIT_KEY, no de un literal en la fila.
    expect(screen.getByText("por 1M tokens")).toBeDefined();
  });
});

describe("model-prices en inglés", () => {
  it("traduce cabecera, filtros y columnas de la tabla", async () => {
    renderIn("en");

    expect(await screen.findByText("Models & Prices")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("prices-table")).toBeTruthy());
    expect(screen.getByText("Family (provider)")).toBeDefined();
    expect(screen.getByText("Current only")).toBeDefined();
    expect(screen.getByRole("button", { name: "Filter" })).toBeDefined();
    expect(screen.getByTestId("price-current-p1").textContent).toContain("current");
    expect(screen.getByText("per 1M tokens")).toBeDefined();

    expect(screen.queryByText("Modelos & Precios")).toBeNull();
    expect(screen.queryByText("Familia (provider)")).toBeNull();
    expect(screen.queryByText("por 1M tokens")).toBeNull();
  });

  it("traduce el aviso de alcance del sync", async () => {
    renderIn("en");

    const notice = await screen.findByTestId("sync-scope-notice");
    expect(notice.textContent).toContain("Syncing only:");
    expect(notice.textContent).toContain("anthropic");
    expect(notice.textContent).not.toContain("Sincronizando");
  });

  it("traduce el diálogo de alta, que vive ya en su propio fichero", async () => {
    renderIn("en");

    fireEvent.click(await screen.findByTestId("price-create-open"));
    await waitFor(() => expect(screen.getByTestId("price-form-dialog")).toBeTruthy());

    const dialog = within(screen.getByTestId("price-form-dialog"));
    expect(dialog.getByText("New price")).toBeDefined();
    expect(dialog.getByText("Cache input (USD, optional)")).toBeDefined();
    expect(screen.getByTestId("price-form-usd-note").textContent).toContain("per 1M tokens");
    expect(dialog.getByRole("button", { name: "Create" })).toBeDefined();
    expect(screen.queryByText(/Precios en USD canónico/)).toBeNull();
  });

  it("traduce el diálogo del histórico, que vive ya en su propio fichero", async () => {
    renderIn("en");

    fireEvent.click(await screen.findByTestId("price-history-p1"));
    await waitFor(() => expect(screen.getByTestId("price-history-dialog")).toBeTruthy());

    const dialog = within(screen.getByTestId("price-history-dialog"));
    expect(await dialog.findByText("From")).toBeDefined();
    expect(dialog.getByText("To")).toBeDefined();
    expect(screen.getByLabelText("Price-over-time chart")).toBeDefined();
    expect(dialog.queryByText("Desde")).toBeNull();
  });
});
