// @vitest-environment jsdom
// Enrolamiento TOTP con QR (MFA UI, tanda 2026-07-19). El backend del Plan
// 08 expone GET /auth/mfa/totp (estado), POST /auth/mfa/totp/enroll
// (secret + otpauth:// URI + recovery codes de un solo vistazo), POST
// /auth/mfa/totp/confirm (activa) y DELETE /auth/mfa/totp (desactiva). La
// pantalla los recorre: sin factor → activar → QR + códigos → confirmar →
// activo; el QR se renderiza con qrcode.react (nunca a mano).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import SecuritySettingsPage from "@/app/admin/settings/security/page";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SecuritySettingsPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SecuritySettingsPage (TOTP)", () => {
  it("sin factor: muestra 'no activado' y el enrolamiento enseña QR + recovery codes", async () => {
    apiFetchMock
      // GET estado inicial
      .mockResolvedValueOnce({ enrolled: false, confirmed: false, recovery_codes_remaining: 0 })
      // POST enroll
      .mockResolvedValueOnce({
        secret: "JBSWY3DPEHPK3PXP",
        provisioning_uri: "otpauth://totp/Agentic:ana@example.com?secret=JBSWY3DPEHPK3PXP",
        recovery_codes: ["aaaa-1111", "bbbb-2222"],
      });
    renderPage();

    expect(await screen.findByTestId("mfa-status-off")).toBeTruthy();
    fireEvent.click(screen.getByTestId("mfa-enroll-button"));

    // QR renderizado (qrcode.react) + secret manual + códigos de un solo vistazo.
    await screen.findByTestId("mfa-qr");
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeTruthy();
    expect(screen.getByText("aaaa-1111")).toBeTruthy();
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/auth/mfa/totp/enroll",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("confirmar con el código activa el factor", async () => {
    apiFetchMock
      .mockResolvedValueOnce({ enrolled: false, confirmed: false, recovery_codes_remaining: 0 })
      .mockResolvedValueOnce({
        secret: "JBSWY3DPEHPK3PXP",
        provisioning_uri: "otpauth://totp/x?secret=JBSWY3DPEHPK3PXP",
        recovery_codes: ["aaaa-1111"],
      })
      // POST confirm → estado confirmado
      .mockResolvedValueOnce({ enrolled: true, confirmed: true, recovery_codes_remaining: 1 });
    renderPage();

    fireEvent.click(await screen.findByTestId("mfa-enroll-button"));
    await screen.findByTestId("mfa-qr");

    fireEvent.change(screen.getByTestId("mfa-confirm-input"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByTestId("mfa-confirm-button"));

    await screen.findByTestId("mfa-status-on");
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/auth/mfa/totp/confirm",
      expect.objectContaining({ method: "POST", body: { code: "123456" } }),
    );
  });

  it("con factor confirmado: muestra activo y permite desactivarlo", async () => {
    apiFetchMock
      .mockResolvedValueOnce({ enrolled: true, confirmed: true, recovery_codes_remaining: 3 })
      // DELETE
      .mockResolvedValueOnce(undefined)
      // refetch del estado
      .mockResolvedValueOnce({ enrolled: false, confirmed: false, recovery_codes_remaining: 0 });
    renderPage();

    expect(await screen.findByTestId("mfa-status-on")).toBeTruthy();
    fireEvent.click(screen.getByTestId("mfa-disable-button"));

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/auth/mfa/totp",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });
});
