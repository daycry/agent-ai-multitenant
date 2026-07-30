// @vitest-environment jsdom
// Plan 08 / ADR 0047 `task_sso_03` — pantalla "sin permisos".
//
// Acredita el extremo deny-by-default de la resolución post-login: un usuario
// autenticado SIN ninguna membership ve la pantalla que le manda al
// administrador y NO entra en la aplicación. El acceso a un tenant lo concede
// exclusivamente una membership que asigna un admin: no hay reclamación por
// dominio de email ni auto-alta.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  // `ApiError` real: la página distingue el 401 esperado de un fallo
  // inesperado con `instanceof`, así que el doble tiene que ser la clase.
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: replaceMock }) }));

const getTokenMock = vi.fn<() => string | null>();
const clearTokenMock = vi.fn();
vi.mock("@/lib/auth", () => ({
  getToken: () => getTokenMock(),
  clearToken: (...a: unknown[]) => clearTokenMock(...a),
}));

const clearTenantIdMock = vi.fn();
vi.mock("@/lib/tenant-storage", () => ({
  clearTenantId: (...a: unknown[]) => clearTenantIdMock(...a),
}));

import { ApiError } from "@/lib/api";
import NoAccessPage from "@/app/no-access/page";

beforeEach(() => {
  getTokenMock.mockReturnValue("identity-token");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("pantalla de sin permisos", () => {
  it("muestra el mensaje de contactar con el administrador", () => {
    render(<NoAccessPage />);

    expect(screen.getByTestId("no-access-screen")).not.toBeNull();
    expect(screen.getByText("Sin acceso a la plataforma")).not.toBeNull();
    const copy = screen.getByTestId("no-access-screen").textContent ?? "";
    expect(copy).toContain("No tienes permisos asignados");
    expect(copy).toContain("Contacta con el administrador");
  });

  it("NO entra en la aplicación: no navega a ninguna ruta /admin", () => {
    render(<NoAccessPage />);

    expect(replaceMock).not.toHaveBeenCalled();
    // Nada de la app: la pantalla es un cul-de-sac con un único botón.
    expect(screen.queryByTestId("sidebar-nav")).toBeNull();
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("data-testid"))).toEqual([
      "no-access-logout",
    ]);
  });

  it("el botón cierra sesión: POST /auth/logout, limpia token + tenant y vuelve a /login", async () => {
    apiFetchMock.mockResolvedValueOnce(undefined);
    render(<NoAccessPage />);

    fireEvent.click(screen.getByTestId("no-access-logout"));

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(apiFetchMock).toHaveBeenCalledWith("/auth/logout", { method: "POST" });
    expect(clearTokenMock).toHaveBeenCalledTimes(1);
    expect(clearTenantIdMock).toHaveBeenCalledTimes(1);
  });

  it("aunque el logout del backend falle, la sesión local se limpia igual", async () => {
    // Caso realista: el token ya no vale y el backend responde 401.
    apiFetchMock.mockRejectedValueOnce(new ApiError(401, "unauthorized"));
    render(<NoAccessPage />);

    fireEvent.click(screen.getByTestId("no-access-logout"));

    await waitFor(() => expect(clearTokenMock).toHaveBeenCalledTimes(1));
    expect(clearTenantIdMock).toHaveBeenCalledTimes(1);
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  it("un acceso directo sin token rebota al login", async () => {
    getTokenMock.mockReturnValue(null);
    render(<NoAccessPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });
});
