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
const searchParamsMock = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => searchParamsMock,
}));

// ADR 0133: el login ya no guarda nada — la sesión llega como cookie httpOnly
// en la respuesta del propio POST. El mock de `@/lib/auth` desaparece: si
// alguien repone un `setToken`, el import revienta.
vi.mock("@/lib/session", () => ({
  resolveAndRoute: vi.fn(async () => "/admin/dashboard"),
  HOME_ROUTE: "/admin/dashboard",
}));
// Los botones SSO hacen su propio fetch de providers — fuera de este test.
vi.mock("@/components/login/provider-buttons", () => ({ ProviderButtons: () => null }));

import LoginPage, { safeNextRoute } from "@/app/login/page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Desde prod-16 `task_prod16_01` el login se traduce por diccionario y su
// idioma por defecto es ES, así que los selectores NO pueden ir contra el texto
// inglés. Se buscan por expresión regular en los dos idiomas: lo que este test
// comprueba es el flujo MFA, no la copia (ese contrato vive en `i18n.test.tsx`).
const PASSWORD_LABEL = /^(password|contraseña)$/i;
const SUBMIT_BUTTON = /^(sign in|iniciar sesión)$/i;

async function submitPassword() {
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: "ana@example.com" },
  });
  fireEvent.change(screen.getByLabelText(PASSWORD_LABEL), {
    target: { value: "secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: SUBMIT_BUTTON }));
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
    expect(screen.queryByLabelText(PASSWORD_LABEL)).toBeNull();

    fireEvent.change(codeInput, { target: { value: "654321" } });
    fireEvent.submit(screen.getByTestId("mfa-form"));

    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/admin/dashboard"));
    expect(apiFetchMock).toHaveBeenLastCalledWith(
      "/auth/mfa/totp/verify",
      expect.objectContaining({
        body: { mfa_token: "challenge-1", code: "654321" },
      }),
    );
  });
});

/**
 * `?next=` is written by `middleware.ts` and by the global 401 handler, but it
 * arrives in the URL — so it is attacker-supplied. Sending a freshly
 * authenticated browser to an attacker's origin is the textbook open redirect,
 * and "starts with a slash" is NOT enough to prevent it: `//evil.example` also
 * starts with a slash and the browser reads it as protocol-relative.
 */
describe("safeNextRoute", () => {
  it("accepts a server-relative path", () => {
    expect(safeNextRoute("/admin/plans/abc?tab=tasks")).toBe("/admin/plans/abc?tab=tasks");
  });

  it("rejects an absolute URL", () => {
    expect(safeNextRoute("https://evil.example/")).toBeNull();
  });

  it("rejects a protocol-relative URL", () => {
    expect(safeNextRoute("//evil.example/")).toBeNull();
  });

  it("rejects the backslash variant browsers also normalise", () => {
    expect(safeNextRoute("/\\evil.example")).toBeNull();
  });

  it("returns null when there is no parameter", () => {
    expect(safeNextRoute(null)).toBeNull();
    expect(safeNextRoute("")).toBeNull();
  });
});
