// @vitest-environment jsdom
/**
 * El indicador de salud sólo aparece cuando algo duele (`task_ui_01`).
 *
 * Las dos reglas que este componente tiene que cumplir no son estéticas:
 *
 * 1. **Sólo el System Admin lo consulta.** `/admin/system-health` depende de
 *    `require_system_admin`; pedirlo para un tenant_admin serían 403 en cada
 *    carga de página, y el test lo afirma sobre `apiFetch`, no sobre el render.
 * 2. **Verde no se pinta.** Un indicador permanente es ruido que se aprende a
 *    ignorar justo antes de que se ponga rojo.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/lib/lang-context", () => ({
  useLang: () => ({ lang: "es", setLang: vi.fn() }),
  useLangOptional: () => "es",
}));

import {
  degradedServices,
  SystemHealthIndicator,
  type SystemHealthResponse,
} from "@/components/layout/system-health-indicator";

const ALL_OK: SystemHealthResponse = {
  status: "ok",
  services: [
    { name: "postgres", status: "ok" },
    { name: "redis", status: "ok" },
  ],
};

const TWO_DOWN: SystemHealthResponse = {
  status: "degraded",
  services: [
    { name: "postgres", status: "ok" },
    { name: "clamav", status: "down", detail: "connection failed" },
    { name: "ollama", status: "degraded" },
  ],
};

function renderIndicator(isSystemAdmin: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SystemHealthIndicator isSystemAdmin={isSystemAdmin} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("degradedServices", () => {
  it("no cuenta nada cuando todo está ok", () => {
    expect(degradedServices(ALL_OK)).toEqual([]);
  });

  it("cuenta lo degradado Y lo caído, que son dos estados distintos", () => {
    expect(degradedServices(TWO_DOWN).map((s) => s.name)).toEqual(["clamav", "ollama"]);
  });

  it("no revienta sin datos (la primera carga, o una sonda que falló)", () => {
    expect(degradedServices(undefined)).toEqual([]);
    expect(degradedServices({ status: "ok" } as SystemHealthResponse)).toEqual([]);
  });
});

describe("SystemHealthIndicator", () => {
  it("no consulta el endpoint si no eres System Admin", async () => {
    apiFetchMock.mockResolvedValue(TWO_DOWN);
    renderIndicator(false);
    // Ni pinta ni pregunta: el endpoint es del System Admin.
    expect(screen.queryByTestId("system-health-indicator")).toBeNull();
    await waitFor(() => expect(apiFetchMock).not.toHaveBeenCalled());
  });

  it("no pinta nada con el stack entero en verde", async () => {
    apiFetchMock.mockResolvedValue(ALL_OK);
    renderIndicator(true);
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledWith("/admin/system-health"));
    expect(screen.queryByTestId("system-health-indicator")).toBeNull();
  });

  it("aparece con el número de servicios con problemas y enlaza al detalle", async () => {
    apiFetchMock.mockResolvedValue(TWO_DOWN);
    renderIndicator(true);
    const pill = await screen.findByTestId("system-health-indicator");
    expect(pill.textContent).toContain("2");
    expect(pill.getAttribute("href")).toBe("/admin/dashboard");
  });

  it("no pinta nada si la sonda falla: no se sabe no es lo mismo que va mal", async () => {
    apiFetchMock.mockRejectedValue(new Error("boom"));
    renderIndicator(true);
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    expect(screen.queryByTestId("system-health-indicator")).toBeNull();
  });
});
