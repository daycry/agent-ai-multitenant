// @vitest-environment jsdom

/**
 * El dashboard de guardrails, migrado al diccionario (prod-16 `task_prod16_04`).
 *
 * Lo que se afirma además de la traducción: que los `guardrail_type` y los
 * `hook_point` NO se traducen. Son los slugs del backend, y el operador los
 * busca tal cual en los logs y en la configuración de guardrails; traducirlos
 * rompería esa correspondencia. Las ACCIONES sí se traducen, porque el panel ya
 * las mostraba en castellano y dejarlas a medias era peor.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import GuardrailsDashboardPage from "@/app/admin/guardrails/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const DASHBOARD = {
  total: 4,
  window_days: 30,
  by_type: [{ guardrail_type: "secret_leak", count: 4 }],
  by_severity: [{ severity: "high", count: 4 }],
  by_day: [{ day: "2026-08-01", count: 4 }],
  recent: [
    {
      id: "ev-1",
      tenant_id: "t1",
      guardrail_type: "secret_leak",
      hook_point: "post_llm",
      severity: "high",
      action: "redact",
      project_id: null,
      agent_id: null,
      execution_id: null,
      agent_label: null,
      detail: "sk-***",
      detail_payload: {},
      created_at: "2026-08-01T10:00:00Z",
    },
  ],
};

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation(() => Promise.resolve(DASHBOARD));
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <GuardrailsDashboardPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("guardrails en castellano", () => {
  it("rinde cabecera, desgloses y la tabla de recientes", async () => {
    renderIn("es");

    expect(await screen.findByText("Eventos (30d)")).toBeDefined();
    expect(screen.getByText("Tendencia diaria")).toBeDefined();
    expect(screen.getByText("Por tipo")).toBeDefined();
    expect(screen.getByText("Por severidad")).toBeDefined();
    expect(screen.getByText("Detalle (enmascarado)")).toBeDefined();
    expect(within(screen.getByTestId("event-row-ev-1")).getByText("enmascarar")).toBeDefined();
  });
});

describe("guardrails en inglés", () => {
  it("rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en");

    expect(await screen.findByText("Events (30d)")).toBeDefined();
    expect(screen.getByText("Daily trend")).toBeDefined();
    expect(screen.getByText("By type")).toBeDefined();
    expect(screen.getByText("By severity")).toBeDefined();
    expect(screen.getByText("Detail (masked)")).toBeDefined();
    expect(screen.getByLabelText("Events per day")).toBeDefined();

    expect(screen.queryByText("Tendencia diaria")).toBeNull();
    expect(screen.queryByText("Detalle (enmascarado)")).toBeNull();
  });

  it("traduce la ACCIÓN pero NO el tipo ni el hook, que son slugs del backend", async () => {
    renderIn("en");

    const row = within(await screen.findByTestId("event-row-ev-1"));
    expect(row.getByText("redact")).toBeDefined();
    // Los slugs se quedan como los devuelve el backend, en los dos idiomas.
    expect(row.getByText("secret_leak")).toBeDefined();
    expect(row.getByText("post_llm")).toBeDefined();
  });
});
