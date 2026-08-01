// @vitest-environment jsdom

/**
 * Las pantallas de `settings/` que NO dependen del registry, migradas al
 * diccionario (plan prod-16, `task_prod16_03`).
 *
 * Son `security` (alta/baja del segundo factor) y `hourly-rate` (tarifa del
 * cálculo de coste humano). **No** están aquí `settings/page.tsx` ni
 * `settings/memories/page.tsx` a propósito: sus títulos y descripciones los
 * sirve el backend en `label_es`/`description_es`
 * (`api_server/settings_registry.py`) y NO existe el par `_en`, así que
 * traducir sólo el marco dejaría la pantalla mitad en inglés y mitad en
 * castellano — exactamente el fallo que prod-16 viene a cerrar. Queda
 * reportado como bloqueo de backend.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

import HourlyRatePage from "@/app/admin/settings/hourly-rate/page";
import SecuritySettingsPage from "@/app/admin/settings/security/page";

const STORAGE_KEY = "admin-panel.lang";

function routeApi(path: string): unknown {
  if (path === "/auth/mfa/totp") {
    return { enrolled: false, confirmed: false, recovery_codes_remaining: 0 };
  }
  if (path === "/auth/mfa/totp/enroll") {
    return {
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri: "otpauth://totp/demo",
      recovery_codes: ["aaaa-1111"],
    };
  }
  if (path === "/tenant-settings/hourly-rate") {
    return { hourly_rate: "50.00", hourly_rate_currency: "EUR" };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  apiFetchMock.mockImplementation((path: string) => Promise.resolve(routeApi(path)));
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("settings/security — verificación en dos pasos", () => {
  it("en castellano rinde el estado apagado y los tres pasos del alta", async () => {
    renderIn("es", <SecuritySettingsPage />);

    expect(await screen.findByText("Seguridad")).toBeDefined();
    expect(screen.getAllByText("Verificación en dos pasos").length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByTestId("mfa-enroll-button"));
    expect(await screen.findByText("3 · Confirma con el código de la app")).toBeDefined();
    expect(screen.getByLabelText("Código")).toBeDefined();
    expect(screen.getByRole("button", { name: "Confirmar" })).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <SecuritySettingsPage />);

    expect(await screen.findByText("Security")).toBeDefined();
    expect(screen.getAllByText("Two-step verification").length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByTestId("mfa-enroll-button"));
    expect(await screen.findByText("3 · Confirm with the app's code")).toBeDefined();
    expect(screen.getByLabelText("Code")).toBeDefined();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDefined();

    expect(screen.queryByText("Seguridad")).toBeNull();
    expect(screen.queryByLabelText("Código")).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirmar" })).toBeNull();
  });
});

describe("settings/hourly-rate — tarifa horaria", () => {
  it("en castellano rinde cabecera, campos y botón", async () => {
    renderIn("es", <HourlyRatePage />);

    expect(await screen.findByText("Tarifa horaria del tenant")).toBeDefined();
    expect(await screen.findByLabelText("Tarifa por hora")).toBeDefined();
    expect(screen.getByLabelText("Moneda")).toBeDefined();
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <HourlyRatePage />);

    expect(await screen.findByText("Tenant hourly rate")).toBeDefined();
    expect(await screen.findByLabelText("Rate per hour")).toBeDefined();
    expect(screen.getByLabelText("Currency")).toBeDefined();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();

    expect(screen.queryByText("Tarifa horaria del tenant")).toBeNull();
    expect(screen.queryByLabelText("Moneda")).toBeNull();
    expect(screen.queryByRole("button", { name: "Guardar" })).toBeNull();
  });
});
