// @vitest-environment jsdom

/**
 * Las DOS piezas que cuelgan del login, migradas al diccionario
 * (plan prod-16, `task_prod16_02`).
 *
 * `app/login/page.tsx` llevaba migrado desde el 08-01 y su
 * `app/login/i18n.test.tsx` lo daba por cerrado… mientras la pantalla seguía
 * saliendo mitad-y-mitad, que es justo el fallo que este plan cierra:
 *
 *   * `MfaChallenge` —el segundo factor— estaba entero en castellano cableado.
 *     Con el toggle en EN, quien tiene TOTP activado veía «Código de
 *     verificación» y «Verificar» justo en el paso en el que más se lee.
 *   * `ProviderButtons` escribía el separador «o continúa con» en castellano
 *     fijo, y `provider-brand.tsx` los cinco textos de respaldo en INGLÉS fijo
 *     («Sign in with Microsoft»), o sea el mismo defecto en el otro sentido:
 *     con el panel en castellano —que es el idioma por defecto— el botón de
 *     SSO salía en inglés desde el día 1.
 *
 * Ninguna de las dos guardas de `check-i18n.mjs` podía verlo: no hay ternario
 * de comparación de idioma y el castellano de `MfaChallenge` vive en texto JSX suelto,
 * no en atributos. Y el test que ya existía **mockeaba `ProviderButtons` a
 * `null`**, así que el separador nunca llegó a renderizarse en una aserción.
 * Es la variante de «un módulo migrado que importa un componente sin migrar no
 * está migrado» que la ola 7 anotó, aquí con el test cómplice.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MfaChallenge } from "@/components/login/mfa-challenge";
import { ProviderButtons } from "@/components/login/provider-buttons";

const STORAGE_KEY = "admin-panel.lang";

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  window.localStorage.setItem(STORAGE_KEY, lang);
  return render(<LanguageProvider>{node}</LanguageProvider>);
}

/** Los providers públicos que sirve `GET /auth/sso/providers` (sin secretos). */
function wireProviders(providers: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => providers })) as unknown as typeof fetch,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("segundo factor (MfaChallenge) en los dos idiomas", () => {
  it("rinde ayuda, etiqueta y botón en castellano", () => {
    renderIn("es", <MfaChallenge mfaToken="challenge-abc" onSuccess={vi.fn()} />);

    expect(screen.getByText(/Introduce el código de tu app de autenticación/)).toBeDefined();
    expect(screen.getByLabelText("Código de verificación")).toBeDefined();
    expect(screen.getByRole("button", { name: "Verificar" })).toBeDefined();
  });

  it("traduce ayuda, etiqueta y botón, y no deja castellano por debajo", () => {
    renderIn("en", <MfaChallenge mfaToken="challenge-abc" onSuccess={vi.fn()} />);

    expect(screen.getByText(/Enter the code from your authenticator app/)).toBeDefined();
    expect(screen.getByLabelText("Verification code")).toBeDefined();
    expect(screen.getByRole("button", { name: "Verify" })).toBeDefined();

    expect(screen.queryByText(/Introduce el código de tu app de autenticación/)).toBeNull();
    expect(screen.queryByLabelText("Código de verificación")).toBeNull();
    expect(screen.queryByRole("button", { name: "Verificar" })).toBeNull();
  });

  it("traduce el error de código inválido (401)", async () => {
    const { ApiError } = await import("@/lib/api");
    apiFetchMock.mockRejectedValueOnce(new ApiError(401, "invalid code"));
    renderIn("en", <MfaChallenge mfaToken="challenge-abc" onSuccess={vi.fn()} />);

    fireEvent.change(screen.getByTestId("mfa-code-input"), { target: { value: "000000" } });
    fireEvent.submit(screen.getByTestId("mfa-form"));

    await waitFor(() => expect(screen.getByTestId("mfa-error")).toBeTruthy());
    expect(screen.getByTestId("mfa-error").textContent).toBe(
      "Incorrect code. Try again or use a recovery code.",
    );
  });

  it("traduce el error del challenge caducado (410)", async () => {
    const { ApiError } = await import("@/lib/api");
    apiFetchMock.mockRejectedValueOnce(new ApiError(410, "expired"));
    renderIn("en", <MfaChallenge mfaToken="challenge-abc" onSuccess={vi.fn()} />);

    fireEvent.change(screen.getByTestId("mfa-code-input"), { target: { value: "000000" } });
    fireEvent.submit(screen.getByTestId("mfa-form"));

    await waitFor(() => expect(screen.getByTestId("mfa-error")).toBeTruthy());
    expect(screen.getByTestId("mfa-error").textContent).toBe(
      "The challenge has expired. Please sign in again.",
    );
  });

  it("mantiene el error en castellano cuando el idioma es ES", async () => {
    const { ApiError } = await import("@/lib/api");
    apiFetchMock.mockRejectedValueOnce(new ApiError(401, "invalid code"));
    renderIn("es", <MfaChallenge mfaToken="challenge-abc" onSuccess={vi.fn()} />);

    fireEvent.change(screen.getByTestId("mfa-code-input"), { target: { value: "000000" } });
    fireEvent.submit(screen.getByTestId("mfa-form"));

    await waitFor(() => expect(screen.getByTestId("mfa-error")).toBeTruthy());
    expect(screen.getByTestId("mfa-error").textContent).toContain("Código incorrecto");
  });
});

describe("botones de SSO (ProviderButtons) en los dos idiomas", () => {
  const OIDC_NO_LABEL = {
    id: "p-1",
    kind: "oidc",
    display_name: "Acme Entra ID",
    button_label: null,
    login_url: "/auth/sso/oidc/p-1/login",
  };

  it("rinde el separador en castellano", async () => {
    wireProviders([OIDC_NO_LABEL]);
    renderIn("es", <ProviderButtons />);

    await waitFor(() => expect(screen.getByTestId("login-divider")).toBeTruthy());
    expect(screen.getByTestId("login-divider").textContent).toBe("o continúa con");
  });

  it("traduce el separador y no deja el castellano por debajo", async () => {
    wireProviders([OIDC_NO_LABEL]);
    renderIn("en", <ProviderButtons />);

    await waitFor(() => expect(screen.getByTestId("login-divider")).toBeTruthy());
    expect(screen.getByTestId("login-divider").textContent).toBe("or continue with");
    expect(screen.queryByText("o continúa con")).toBeNull();
  });

  /**
   * El respaldo de marca era el defecto en el OTRO sentido: inglés cableado en
   * el idioma por DEFECTO del panel. Microsoft, Google y GitHub publican su
   * texto de «sign in» traducido justo para esto, así que traducirlo no rompe
   * ninguna guía de marca — dejarlo en inglés sí rompía la pantalla.
   */
  it("traduce el texto de respaldo de la marca cuando el operador no puso etiqueta", async () => {
    wireProviders([OIDC_NO_LABEL]);
    renderIn("es", <ProviderButtons />);

    const button = await screen.findByTestId("login-provider-p-1");
    expect(button.textContent).toBe("Iniciar sesión con Microsoft");
    expect(button.getAttribute("aria-label")).toBe("Iniciar sesión con Microsoft");
  });

  it("usa el respaldo inglés con el toggle en EN", async () => {
    wireProviders([OIDC_NO_LABEL]);
    renderIn("en", <ProviderButtons />);

    const button = await screen.findByTestId("login-provider-p-1");
    expect(button.textContent).toBe("Sign in with Microsoft");
  });

  it("cubre las cinco marcas, incluido el respaldo neutro de SAML", async () => {
    wireProviders([
      { ...OIDC_NO_LABEL, id: "g", display_name: "Acme Google Workspace" },
      { ...OIDC_NO_LABEL, id: "gh", display_name: "Acme GitHub" },
      { ...OIDC_NO_LABEL, id: "n", display_name: "Acme IdP" },
      { ...OIDC_NO_LABEL, id: "s", kind: "saml", display_name: "Acme IdP" },
    ]);
    renderIn("es", <ProviderButtons />);

    expect((await screen.findByTestId("login-provider-g")).textContent).toBe(
      "Iniciar sesión con Google",
    );
    expect(screen.getByTestId("login-provider-gh").textContent).toBe("Iniciar sesión con GitHub");
    expect(screen.getByTestId("login-provider-n").textContent).toBe("Iniciar sesión con SSO");
    expect(screen.getByTestId("login-provider-s").textContent).toBe("Iniciar sesión con SSO");
  });

  /**
   * La etiqueta que escribe el OPERADOR manda sobre el respaldo traducido: la
   * escribió una persona para su IdP y traducirla sería inventarse su texto.
   */
  it("respeta la etiqueta del operador por encima del respaldo traducido", async () => {
    wireProviders([{ ...OIDC_NO_LABEL, button_label: "Entrar con el IdP de Acme" }]);
    renderIn("en", <ProviderButtons />);

    const button = await screen.findByTestId("login-provider-p-1");
    expect(button.textContent).toBe("Entrar con el IdP de Acme");
  });
});
