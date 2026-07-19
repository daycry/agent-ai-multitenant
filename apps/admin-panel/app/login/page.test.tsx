// @vitest-environment jsdom
// Login con segundo factor (MFA UI, tanda 2026-07-19): si /auth/login
// responde `mfa_required` (backend Plan 08), el formulario de password da
// paso al desafío TOTP en la MISMA tarjeta; al verificar, la sesión se
// guarda y se enruta igual que un login directo.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const setTokenMock = vi.fn();
vi.mock("@/lib/auth", () => ({ setToken: (...a: unknown[]) => setTokenMock(...a) }));
vi.mock("@/lib/session", () => ({ resolveAndRoute: vi.fn(async () => "/admin/dashboard") }));
// Los botones SSO hacen su propio fetch de providers — fuera de este test.
vi.mock("@/components/login/provider-buttons", () => ({ ProviderButtons: () => null }));

import LoginPage from "@/app/login/page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function submitPassword() {
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "ana@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("LoginPage + MFA", () => {
  it("un login sin MFA entra directo (sin desafío)", async () => {
    apiFetchMock.mockResolvedValueOnce({
      access_token: "tok-1",
      token_type: "bearer",
      expires_in: 3600,
    });
    render(<LoginPage />);
    await submitPassword();
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/admin/dashboard"));
    expect(setTokenMock).toHaveBeenCalledWith("tok-1");
  });

  it("mfa_required muestra el desafío y la verificación completa la sesión", async () => {
    apiFetchMock
      .mockResolvedValueOnce({
        status: "mfa_required",
        mfa_token: "challenge-1",
        mfa_methods: ["totp"],
      })
      .mockResolvedValueOnce({
        access_token: "tok-2",
        token_type: "bearer",
        expires_in: 3600,
      });
    render(<LoginPage />);
    await submitPassword();

    // El desafío sustituye al formulario de password.
    const codeInput = await screen.findByTestId("mfa-code-input");
    expect(screen.queryByLabelText("Password")).toBeNull();

    fireEvent.change(codeInput, { target: { value: "654321" } });
    fireEvent.submit(screen.getByTestId("mfa-form"));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/admin/dashboard"));
    expect(setTokenMock).toHaveBeenCalledWith("tok-2");
    expect(apiFetchMock).toHaveBeenLastCalledWith(
      "/auth/mfa/totp/verify",
      expect.objectContaining({
        body: { mfa_token: "challenge-1", code: "654321" },
      }),
    );
  });
});
