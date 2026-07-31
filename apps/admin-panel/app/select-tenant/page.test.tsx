// @vitest-environment jsdom
// Plan 08 / ADR 0047 `task_sso_03` — selector de tenant POST-LOGIN.
//
// TRAMPA DOCUMENTADA: `e2e/tenant-picker.spec.ts` PARECE cubrir esto y no lo
// cubre. Ese spec ejercita el picker de la CABECERA, que es el override
// `X-Tenant-Id` del superadmin (`components/layout/tenant-picker.tsx`); esta
// pantalla es otra cosa: vive FUERA del shell `/admin`, es para un usuario
// normal con VARIAS memberships, y su elección hace POST
// /auth/session/select-tenant para que el backend re-afirme la membership y
// acuñe un token con ámbito de ESE tenant (un usuario normal no puede usar el
// override de cabecera). Sin este test nadie acreditaba la pantalla.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const resolveSessionMock = vi.fn();
const selectTenantMock = vi.fn();
const applySingleResolutionMock = vi.fn();
vi.mock("@/lib/session", async (importOriginal) => {
  // Las CONSTANTES de ruta son las de verdad: si cambiaran, este test debe
  // seguir afirmando el destino real, no una copia mía.
  const actual = await importOriginal<typeof import("@/lib/session")>();
  return {
    ...actual,
    resolveSession: () => resolveSessionMock(),
    selectTenant: (...a: unknown[]) => selectTenantMock(...a),
    applySingleResolution: (...a: unknown[]) => applySingleResolutionMock(...a),
  };
});

const replaceMock = vi.fn();
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: pushMock }),
}));

// ADR 0133: la pantalla ya no lee un token — sólo pregunta si hay sesión
// (cookie CSRF legible); el gate duro es `middleware.ts`.
const hasSessionMock = vi.fn<() => boolean>();
vi.mock("@/lib/auth", () => ({ hasSession: () => hasSessionMock() }));

import { HOME_ROUTE, NO_ACCESS_ROUTE, type SessionResolution } from "@/lib/session";
import SelectTenantPage from "@/app/select-tenant/page";

const TENANT_A = { tenant_id: "t-aaa", tenant_name: "Tenant A", role: "tenant_admin" };
const TENANT_B = { tenant_id: "t-bbb", tenant_name: "Tenant B", role: "tenant_user" };

const resolution = (over: Partial<SessionResolution>): SessionResolution => ({
  state: "multiple",
  memberships: [TENANT_A, TENANT_B],
  access_token: null,
  token_type: null,
  expires_in: null,
  ...over,
});

beforeEach(() => {
  hasSessionMock.mockReturnValue(true);
  resolveSessionMock.mockResolvedValue(resolution({}));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("pantalla de elección de espacio de trabajo", () => {
  it("lista los tenants del usuario con su rol", async () => {
    render(<SelectTenantPage />);

    const optionA = await screen.findByTestId(`select-tenant-option-${TENANT_A.tenant_id}`);
    const optionB = screen.getByTestId(`select-tenant-option-${TENANT_B.tenant_id}`);
    expect(optionA.textContent).toContain("Tenant A");
    expect(optionA.textContent).toContain("tenant_admin");
    expect(optionB.textContent).toContain("Tenant B");
    expect(screen.getByText("Elige un espacio de trabajo")).not.toBeNull();
  });

  it("no ofrece tenants a los que el usuario no pertenece", async () => {
    render(<SelectTenantPage />);
    await screen.findByTestId(`select-tenant-option-${TENANT_A.tenant_id}`);

    expect(screen.queryByTestId("select-tenant-option-t-ajeno")).toBeNull();
    // Sólo hay tantas opciones como memberships.
    expect(
      screen.getAllByRole("button").filter((b) => {
        const id = b.getAttribute("data-testid") ?? "";
        return id.startsWith("select-tenant-option-");
      }),
    ).toHaveLength(2);
  });

  it("al elegir uno, activa ESE tenant y entra en la aplicación", async () => {
    selectTenantMock.mockResolvedValueOnce(undefined);
    render(<SelectTenantPage />);

    const optionB = await screen.findByTestId(`select-tenant-option-${TENANT_B.tenant_id}`);
    fireEvent.click(optionB);

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith(HOME_ROUTE));
    // El tenant activado es el pulsado, no el primero de la lista.
    expect(selectTenantMock).toHaveBeenCalledWith(TENANT_B.tenant_id);
    expect(selectTenantMock).not.toHaveBeenCalledWith(TENANT_A.tenant_id);
  });

  it("si el backend rechaza la activación, avisa y NO entra", async () => {
    selectTenantMock.mockRejectedValueOnce(new Error("403"));
    render(<SelectTenantPage />);

    fireEvent.click(await screen.findByTestId(`select-tenant-option-${TENANT_A.tenant_id}`));

    const error = await screen.findByTestId("select-tenant-error");
    expect(error.textContent).toContain("No se pudo activar");
    expect(pushMock).not.toHaveBeenCalled();
  });

  // Deep-links con el estado equivocado: la pantalla re-resuelve al montar.
  it("un usuario sin memberships es reenviado a la pantalla de sin permisos", async () => {
    resolveSessionMock.mockResolvedValueOnce(resolution({ state: "no_access", memberships: [] }));
    render(<SelectTenantPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith(NO_ACCESS_ROUTE));
    expect(screen.queryByTestId(`select-tenant-option-${TENANT_A.tenant_id}`)).toBeNull();
  });

  it("con una sola membership no se pregunta: entra directo con el token acuñado", async () => {
    const single = resolution({
      state: "single",
      memberships: [TENANT_A],
      access_token: "scoped-token",
    });
    resolveSessionMock.mockResolvedValueOnce(single);
    render(<SelectTenantPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith(HOME_ROUTE));
    expect(applySingleResolutionMock).toHaveBeenCalledWith(single);
  });

  it("un acceso directo sin token rebota al login sin llamar al backend", async () => {
    hasSessionMock.mockReturnValue(false);
    render(<SelectTenantPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(resolveSessionMock).not.toHaveBeenCalled();
  });

  it("si la resolución falla, muestra el error en vez de una lista vacía muda", async () => {
    resolveSessionMock.mockRejectedValueOnce(new Error("boom"));
    render(<SelectTenantPage />);

    const error = await screen.findByTestId("select-tenant-error");
    expect(error.textContent).toContain("No se pudo cargar");
  });
});
