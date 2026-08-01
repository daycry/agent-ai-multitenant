// @vitest-environment jsdom

/**
 * `llm-providers`, migrada al diccionario y partida en secciones (plan prod-16,
 * `task_prod16_04` + `task_prod16_08`).
 *
 * La pantalla tenía 996 líneas y TODO el texto cableado en castellano: con el
 * toggle en EN un operador anglófono leía "Nuevo proveedor", "sin credencial",
 * "Probar conexión" y "Esperando autorización en GitHub…". Aquí se afirman las
 * tres piezas en que se partió (tabla, diálogo de alta/edición y diálogo del
 * Device Flow de Copilot) en los dos idiomas.
 *
 * El troceado es refactor mecánico, así que además de traducir esto vigila que
 * ninguna de las tres piezas se quedara por el camino: si un diálogo deja de
 * abrirse, o la tabla pierde una columna, salta aquí y no en producción.
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

import LlmProvidersPage from "@/app/admin/llm-providers/page";

const STORAGE_KEY = "admin-panel.lang";

const COPILOT = {
  id: "prov-1",
  kind: "copilot",
  slug: "copilot-main",
  display_name: "Copilot",
  base_url: null,
  is_active: true,
  config: {},
  secret_vault_path: null,
  has_credential: false,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
};

function renderIn(lang: "es" | "en", providers: unknown[] = [COPILOT]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/admin/llm-providers") return Promise.resolve(providers);
    return Promise.resolve([]);
  });
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <LlmProvidersPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("llm-providers en castellano", () => {
  it("rinde cabecera, tabla y estado de credencial", async () => {
    renderIn("es");

    expect(await screen.findByText("Proveedores LLM")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeTruthy());
    expect(screen.getByText("Credencial")).toBeDefined();
    expect(screen.getByText("sin credencial")).toBeDefined();
    expect(screen.getByText("activo")).toBeDefined();
    expect(screen.getByRole("button", { name: "Probar conexión" })).toBeDefined();
  });

  it("rinde el estado vacío", async () => {
    renderIn("es", []);
    expect(await screen.findByTestId("providers-empty")).toBeDefined();
  });
});

describe("llm-providers en inglés", () => {
  it("traduce cabecera, columnas de la tabla y badges de estado", async () => {
    renderIn("en");

    expect(await screen.findByText("LLM providers")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeTruthy());
    expect(screen.getByText("Credential")).toBeDefined();
    expect(screen.getByText("Endpoint")).toBeDefined();
    expect(screen.getByText("no credential")).toBeDefined();
    expect(screen.getByText("active")).toBeDefined();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDefined();
  });

  it("no deja castellano por debajo en la tabla", async () => {
    renderIn("en");

    await screen.findByText("LLM providers");
    await waitFor(() => expect(screen.getByTestId("providers-table")).toBeTruthy());

    expect(screen.queryByText("Proveedores LLM")).toBeNull();
    expect(screen.queryByText("sin credencial")).toBeNull();
    expect(screen.queryByText("Nuevo proveedor")).toBeNull();
    expect(screen.queryByRole("button", { name: "Probar conexión" })).toBeNull();
  });

  it("traduce el estado vacío", async () => {
    renderIn("en", []);
    const empty = await screen.findByTestId("providers-empty");
    expect(empty.textContent).toContain("No providers configured");
  });

  it("traduce el diálogo de alta, incluidos labels y placeholders", async () => {
    renderIn("en");

    const open = await screen.findByTestId("provider-create-open");
    fireEvent.click(open);

    await waitFor(() => expect(screen.getByTestId("provider-form-dialog")).toBeTruthy());
    // Dentro del diálogo: "Type"/"Name" también son cabeceras de la tabla que
    // sigue montada debajo, así que hay que acotar o el matcher es ambiguo.
    const dialog = within(screen.getByTestId("provider-form-dialog"));
    expect(dialog.getByText("New provider")).toBeDefined();
    expect(dialog.getByText("Type")).toBeDefined();
    expect(dialog.getByText("Name")).toBeDefined();
    expect(dialog.getByText("Provider active")).toBeDefined();
    expect(screen.getByTestId("form-credential-hint").textContent).toContain("Vault");
    expect(dialog.getByRole("button", { name: "Cancel" })).toBeDefined();
    expect(dialog.getByRole("button", { name: "Create" })).toBeDefined();
    // El hint del slug también es texto de UI, no sólo los labels.
    expect(screen.queryByText(/Handle único/)).toBeNull();
  });

  it("traduce el diálogo del Device Flow de Copilot", async () => {
    renderIn("en");

    const open = await screen.findByTestId("provider-device-flow-prov-1");
    fireEvent.click(open);

    await waitFor(() => expect(screen.getByTestId("device-flow-dialog")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Start Device Flow" })).toBeDefined();
    expect(screen.queryByText(/Iniciar Device Flow/)).toBeNull();
  });
});
