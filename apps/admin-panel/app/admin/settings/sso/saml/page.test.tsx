// @vitest-environment jsdom
// Plan 08 — el ACS de SAML que el operador registra en el IdP.
//
// Mitad SAML del mismo test humano que la callback OIDC ("la pantalla muestra
// la callback/ACS a registrar en el IdP con botón de copiar"). Mismo hueco:
// los botones `saml-acs-url-copy` / `saml-sp-entity-id-copy` existían y nadie
// afirmaba el copiado.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    user: { user_id: "u1", email: "admin@a.test", full_name: "Admin", is_system_admin: false },
    isLoading: false,
    isError: false,
    isSystemAdmin: false,
    isSystemOwner: false,
    isTenantAdmin: true,
    isTenantMember: true,
    roleInActiveTenant: "tenant_admin",
  }),
}));

import SamlConfigPage from "@/app/admin/settings/sso/saml/page";

const ACS_URL = "https://agentic.example.com/auth/sso/saml/acs";
const SP_ENTITY_ID = "https://agentic.example.com/saml/metadata";

const writeTextMock = vi.fn<(text: string) => Promise<void>>();

function routeApi(path: string): unknown {
  if (path === "/auth/sso/saml/config") return [];
  if (path === "/auth/sso/saml/sp-metadata") {
    return { sp_entity_id: SP_ENTITY_ID, acs_url: ACS_URL };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SamlConfigPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  writeTextMock.mockReset();
  writeTextMock.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: writeTextMock },
    configurable: true,
    writable: true,
  });
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation(async (path: string) => routeApi(path));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("metadatos del SP a registrar en el IdP SAML", () => {
  it("muestra el ACS y el Entity ID que devuelve el backend", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("saml-acs-url").textContent).toBe(ACS_URL));
    expect(screen.getByTestId("saml-sp-entity-id").textContent).toBe(SP_ENTITY_ID);
  });

  it("copiar el ACS escribe ESA url en el portapapeles y confirma", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("saml-acs-url").textContent).toBe(ACS_URL));

    fireEvent.click(screen.getByTestId("saml-acs-url-copy"));

    await waitFor(() => expect(writeTextMock).toHaveBeenCalledTimes(1));
    expect(writeTextMock).toHaveBeenCalledWith(ACS_URL);
    await waitFor(() =>
      expect(screen.getByTestId("saml-acs-url-copy").textContent).toContain("Copiado"),
    );
  });

  it("cada botón copia SU valor, no el de la fila de al lado", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("saml-acs-url").textContent).toBe(ACS_URL));

    fireEvent.click(screen.getByTestId("saml-sp-entity-id-copy"));

    await waitFor(() => expect(writeTextMock).toHaveBeenCalledTimes(1));
    expect(writeTextMock).toHaveBeenCalledWith(SP_ENTITY_ID);
    expect(writeTextMock).not.toHaveBeenCalledWith(ACS_URL);
  });
});
