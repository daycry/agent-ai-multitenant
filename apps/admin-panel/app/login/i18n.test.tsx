// @vitest-environment jsdom

/**
 * El login, migrado al diccionario i18n (plan prod-16, `task_prod16_01`).
 *
 * Hasta ahora mezclaba los dos idiomas en la MISMA pantalla: "Sign in",
 * "Password" e "Invalid email or password." junto a "Panel de administración
 * multi-tenant" y "Verificación en dos pasos" (hallazgo frontend-9). Estos
 * tests fijan que cada idioma se rinda entero — lo que DEBE pasar, no lo que
 * pasaba.
 *
 * El flujo funcional (password → MFA → sesión) se prueba en `page.test.tsx`.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/session", () => ({ resolveAndRoute: vi.fn(async () => "/admin/dashboard") }));
// Los botones SSO hacen su propio fetch de providers — fuera de este test.
vi.mock("@/components/login/provider-buttons", () => ({ ProviderButtons: () => null }));

import LoginPage from "@/app/login/page";

const STORAGE_KEY = "admin-panel.lang";

function renderLogin(lang: "es" | "en") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  return render(
    <LanguageProvider>
      <LoginPage />
    </LanguageProvider>,
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("login en castellano", () => {
  it("rinde botón, etiquetas y reclamo en castellano", () => {
    renderLogin("es");

    expect(screen.getByRole("button", { name: "Iniciar sesión" })).toBeDefined();
    expect(screen.getByLabelText("Contraseña")).toBeDefined();
    expect(screen.getByText("Panel de administración multi-tenant")).toBeDefined();
  });

  it("no deja colarse el inglés que había hardcodeado", () => {
    renderLogin("es");

    expect(screen.queryByText("Sign in")).toBeNull();
    expect(screen.queryByLabelText("Password")).toBeNull();
  });
});

describe("login en inglés", () => {
  it("rinde botón, etiquetas y reclamo en inglés", () => {
    renderLogin("en");

    expect(screen.getByRole("button", { name: "Sign in" })).toBeDefined();
    expect(screen.getByLabelText("Password")).toBeDefined();
    expect(screen.getByText("Multi-tenant administration panel")).toBeDefined();
  });

  it("no deja el castellano por debajo (el fallo era la pantalla mitad y mitad)", () => {
    renderLogin("en");

    expect(screen.queryByText("Panel de administración multi-tenant")).toBeNull();
    expect(screen.queryByRole("button", { name: "Iniciar sesión" })).toBeNull();
  });
});

describe("contrato con los helpers de e2e", () => {
  it("la etiqueta Email se escribe igual en los dos idiomas", () => {
    renderLogin("es");
    expect(screen.getByLabelText("Email")).toBeDefined();
    cleanup();

    renderLogin("en");
    expect(screen.getByLabelText("Email")).toBeDefined();
  });

  it("los inputs conservan sus id, que es el ancla estable de los specs", () => {
    const { container } = renderLogin("es");

    expect(container.querySelector("#email")).not.toBeNull();
    expect(container.querySelector("#password")).not.toBeNull();
  });
});
