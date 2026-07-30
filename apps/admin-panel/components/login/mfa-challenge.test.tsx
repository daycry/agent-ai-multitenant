// @vitest-environment jsdom
// MFA UI (tanda 2026-07-19): el backend del Plan 08 ya devolvía
// `mfa_required` + mfa_token en el login, pero el panel lo trataba como un
// login roto (no había paso de código). Este componente completa el flujo:
// código TOTP (o de recuperación) → POST /auth/mfa/totp/verify con el
// challenge token → LoginResponse normal hacia el caller.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MfaChallenge } from "@/components/login/mfa-challenge";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MfaChallenge", () => {
  it("verifica el código TOTP contra /auth/mfa/totp/verify y entrega la sesión", async () => {
    apiFetchMock.mockResolvedValueOnce({
      access_token: "tok-123",
      token_type: "bearer",
      expires_in: 3600,
    });
    const onSuccess = vi.fn();
    render(<MfaChallenge mfaToken="challenge-abc" onSuccess={onSuccess} />);

    fireEvent.change(screen.getByTestId("mfa-code-input"), {
      target: { value: "123456" },
    });
    fireEvent.submit(screen.getByTestId("mfa-form"));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/auth/mfa/totp/verify",
      expect.objectContaining({
        method: "POST",
        body: { mfa_token: "challenge-abc", code: "123456" },
      }),
    );
    expect(onSuccess.mock.calls[0][0]).toMatchObject({ access_token: "tok-123" });
  });

  it("muestra el error de un código inválido (401) sin perder el formulario", async () => {
    const { ApiError } = await import("@/lib/api");
    apiFetchMock.mockRejectedValueOnce(new ApiError(401, "invalid code"));
    render(<MfaChallenge mfaToken="challenge-abc" onSuccess={vi.fn()} />);

    fireEvent.change(screen.getByTestId("mfa-code-input"), {
      target: { value: "000000" },
    });
    fireEvent.submit(screen.getByTestId("mfa-form"));

    await waitFor(() => expect(screen.getByTestId("mfa-error")).toBeTruthy());
    // El formulario sigue vivo para reintentar.
    expect(screen.getByTestId("mfa-code-input")).toBeTruthy();
  });
});
