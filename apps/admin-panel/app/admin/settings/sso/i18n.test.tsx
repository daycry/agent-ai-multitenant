// @vitest-environment jsdom

/**
 * El módulo `settings/sso/` migrado al diccionario (plan prod-16,
 * `task_prod16_03`).
 *
 * Entra COMPLETO —las dos pantallas (OIDC y SAML) con sus tarjetas, sus fichas
 * y sus dos diálogos—, que es la única forma de migrarlo que no reproduce el
 * fallo que este plan cierra: traducir sólo la pantalla y dejar el diálogo en
 * castellano deja al operador rellenando un formulario mitad y mitad justo
 * donde se juega el acceso al tenant.
 *
 * Cada caso rinde en los DOS idiomas y afirma en ambos sentidos: en inglés, que
 * aparece el texto inglés Y que NO queda su cara castellana. Sin la segunda
 * mitad un `useT()` olvidado pasa desapercibido, porque el resto sí traduce.
 *
 * Los DIÁLOGOS se abren de verdad (no se afirma sobre el botón que los abre):
 * ahí vive más de la mitad del texto del módulo, y es donde un literal olvidado
 * no lo ve nadie hasta que alguien pulsa.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

// La tarjeta de callback parte en dos por rol: el System Admin edita la base
// pública y el prefijo de API; el resto sólo la lee. Las dos mitades tienen
// texto propio, así que el rol es un parámetro del render y no una constante.
let systemAdmin = false;
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    user: {
      user_id: "u1",
      email: "admin@a.test",
      full_name: "Admin",
      is_system_admin: systemAdmin,
    },
    isLoading: false,
    isError: false,
    isSystemAdmin: systemAdmin,
    isSystemOwner: systemAdmin,
    isTenantAdmin: true,
    isTenantMember: true,
    roleInActiveTenant: "tenant_admin",
  }),
}));

import SamlConfigPage from "@/app/admin/settings/sso/saml/page";
import SsoConfigPage from "@/app/admin/settings/sso/page";

const STORAGE_KEY = "admin-panel.lang";

const OIDC_CONFIG = {
  id: "cfg-1",
  provider: "oidc",
  display_name: "Acme Entra ID",
  enabled: true,
  issuer: "https://login.microsoftonline.com/acme/v2.0",
  client_id: "client-abc",
  scopes: ["openid", "email", "profile"],
  claim_mappings: { email: "email", full_name: "name" },
  has_client_secret: true,
  client_secret_source: "encrypted",
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:00:00Z",
};

const SAML_CONFIG = {
  id: "saml-1",
  provider: "saml",
  display_name: "Acme Okta",
  enabled: false,
  idp_entity_id: "https://idp.example.com/saml/metadata",
  idp_sso_url: "https://idp.example.com/saml/sso",
  idp_x509_cert: "MIIDcert",
  name_id_format: "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
  attribute_mappings: { email: "email" },
  sp_x509_cert: null,
  has_sp_private_key: true,
  sp_private_key_source: "encrypted",
  authn_requests_signed: false,
  want_assertions_signed: true,
  want_assertions_encrypted: false,
  want_name_id_encrypted: false,
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:00:00Z",
};

/** Respuestas por endpoint. `oidc`/`saml` deciden si hay config o está vacío. */
function makeRouter({ oidc, saml }: { oidc: boolean; saml: boolean }) {
  return (path: string): unknown => {
    if (path === "/auth/sso/config") return oidc ? [OIDC_CONFIG] : [];
    if (path === "/auth/sso/oidc/callback-url") {
      return { callback_url: "https://agentic.example.com/auth/sso/oidc/callback" };
    }
    if (path === "/auth/sso/public-base-url") {
      return {
        base_url: "http://localhost:8001",
        // Sin override: fuerza el aviso de "sigue usando el valor de arranque".
        is_override: false,
        env_default: "http://localhost:8001",
      };
    }
    if (path === "/auth/sso/api-path-prefix") {
      return { prefix: "", is_override: true, env_default: "" };
    }
    if (path === "/auth/sso/oidc/templates") {
      return [
        {
          template_id: "azure",
          display_name: "Microsoft Entra ID",
          issuer_template: "https://login.microsoftonline.com/{tenant}/v2.0",
          default_scopes: ["openid", "email", "profile"],
          claim_mappings: { email: "email" },
          required_params: ["tenant"],
          notes: null,
        },
      ];
    }
    if (path === "/auth/sso/saml/config") return saml ? [SAML_CONFIG] : [];
    if (path === "/auth/sso/saml/sp-metadata") {
      return {
        sp_entity_id: "https://agentic.example.com/saml/metadata",
        acs_url: "https://agentic.example.com/auth/sso/saml/acs",
      };
    }
    throw new Error(`unexpected endpoint in test: ${path}`);
  };
}

function renderIn(
  lang: "es" | "en",
  node: React.ReactElement,
  opts: { oidc?: boolean; saml?: boolean; asSystemAdmin?: boolean } = {},
) {
  systemAdmin = opts.asSystemAdmin ?? false;
  const router = makeRouter({ oidc: opts.oidc ?? false, saml: opts.saml ?? false });
  apiFetchMock.mockImplementation((path: string) => Promise.resolve(router(path)));
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
  systemAdmin = false;
});

describe("settings/sso — pantalla OIDC", () => {
  it("en castellano rinde cabecera, estado vacío y la tarjeta de callback", async () => {
    renderIn("es", <SsoConfigPage />);

    expect(await screen.findByText("SSO empresarial (OIDC)")).toBeDefined();
    expect(
      screen.getByText(
        "Inicio de sesión único por tenant. Se añade junto al login local — activarlo no lo reemplaza ni lo desactiva.",
      ),
    ).toBeDefined();
    expect((await screen.findByTestId("sso-create-button")).textContent).toContain(
      "Configurar OIDC",
    );
    expect((await screen.findByTestId("sso-empty")).textContent).toContain(
      "Este tenant aún no tiene SSO configurado",
    );
    expect(screen.getByText("URL base pública de la aplicación")).toBeDefined();
    expect(screen.getByTestId("sso-callback-copy").textContent).toContain("Copiar");
    expect((await screen.findByTestId("sso-saml-link")).textContent).toContain(
      "Configura SAML aquí",
    );
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <SsoConfigPage />);

    expect(await screen.findByText("Enterprise SSO (OIDC)")).toBeDefined();
    expect(
      screen.getByText(
        "Single sign-on per tenant. It is added alongside local login — enabling it neither replaces nor disables it.",
      ),
    ).toBeDefined();
    expect((await screen.findByTestId("sso-create-button")).textContent).toContain(
      "Configure OIDC",
    );
    expect((await screen.findByTestId("sso-empty")).textContent).toContain(
      "This tenant has no SSO configured yet",
    );
    expect(screen.getByText("Application public base URL")).toBeDefined();
    expect(screen.getByTestId("sso-callback-copy").textContent).toContain("Copy");
    expect((await screen.findByTestId("sso-saml-link")).textContent).toContain(
      "Configure SAML here",
    );

    expect(screen.queryByText("SSO empresarial (OIDC)")).toBeNull();
    expect(screen.queryByText("URL base pública de la aplicación")).toBeNull();
  });

  it("la mitad de System Admin de la tarjeta de callback también se traduce", async () => {
    renderIn("en", <SsoConfigPage />, { asSystemAdmin: true });

    expect(await screen.findByText("Public base URL")).toBeDefined();
    expect(screen.getByText("API path prefix (reverse proxy)")).toBeDefined();
    expect(screen.getAllByTestId(/sso-(public-base-url|api-path-prefix)-save/).length).toBe(2);
    expect(screen.getByTestId("sso-public-base-url-save").textContent).toContain("Save");
    // El aviso de "sigue con el valor de arranque" es la razón de ser de la
    // pantalla el primer día: si sale en castellano, se lee mal justo ahí.
    expect((await screen.findByTestId("sso-redirect-base-warning")).textContent).toContain(
      "still using the bootstrap value",
    );

    expect(screen.queryByText("URL base pública")).toBeNull();
    expect(screen.queryByText("Prefijo de API (reverse proxy)")).toBeNull();
  });

  it("la ficha de la config traduce badges y acciones", async () => {
    renderIn("en", <SsoConfigPage />, { oidc: true });

    expect((await screen.findByTestId("sso-enabled-badge")).textContent).toContain("active");
    expect(screen.getByTestId("sso-secret-badge").textContent).toContain(
      "secret: encrypted at rest",
    );
    expect(screen.getByTestId("sso-toggle-enabled").textContent).toContain("Disable");

    expect(screen.getByTestId("sso-enabled-badge").textContent).not.toContain("activo");
    expect(screen.getByTestId("sso-secret-badge").textContent).not.toContain("cifrado en reposo");
  });

  it("el diálogo de alta se traduce entero (es donde vive la mitad del texto)", async () => {
    renderIn("en", <SsoConfigPage />);

    fireEvent.click(await screen.findByTestId("sso-create-button"));

    expect(await screen.findByText("Provider template")).toBeDefined();
    expect(screen.getByText("Display name (optional)")).toBeDefined();
    expect(screen.getByText("Scopes (space-separated)")).toBeDefined();
    expect(
      screen.getByText(
        "It is encrypted at rest before being stored; the system never returns it in clear.",
      ),
    ).toBeDefined();
    expect(screen.getByTestId("sso-form-submit").textContent).toContain("Create");
    expect(screen.getByTestId("sso-form-cancel").textContent).toContain("Cancel");

    expect(screen.queryByText("Plantilla de proveedor")).toBeNull();
    expect(screen.queryByText("Nombre visible (opcional)")).toBeNull();
    expect(screen.queryByText("Scopes (separados por espacios)")).toBeNull();
  });
});

describe("settings/sso/saml — pantalla SAML", () => {
  it("en castellano rinde cabecera, metadatos del SP y estado vacío", async () => {
    renderIn("es", <SamlConfigPage />);

    expect(await screen.findByText("SSO empresarial (SAML 2.0)")).toBeDefined();
    expect((await screen.findByTestId("saml-create-button")).textContent).toContain(
      "Configurar SAML",
    );
    expect(screen.getByText("Metadatos del SP (este sistema)")).toBeDefined();
    expect((await screen.findByTestId("saml-empty")).textContent).toContain(
      "Este tenant aún no tiene SAML configurado",
    );
    expect((await screen.findByTestId("saml-oidc-link")).textContent).toContain(
      "Configura OIDC aquí",
    );
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <SamlConfigPage />);

    expect(await screen.findByText("Enterprise SSO (SAML 2.0)")).toBeDefined();
    expect((await screen.findByTestId("saml-create-button")).textContent).toContain(
      "Configure SAML",
    );
    expect(screen.getByText("SP metadata (this system)")).toBeDefined();
    expect((await screen.findByTestId("saml-empty")).textContent).toContain(
      "This tenant has no SAML configured yet",
    );
    expect((await screen.findByTestId("saml-oidc-link")).textContent).toContain(
      "Configure OIDC here",
    );
    // Las dos filas copiables del SP: la etiqueta y el aria-label del botón.
    expect(screen.getByLabelText("Copy SP Entity ID")).toBeDefined();

    expect(screen.queryByText("SSO empresarial (SAML 2.0)")).toBeNull();
    expect(screen.queryByText("Metadatos del SP (este sistema)")).toBeNull();
  });

  it("la ficha SAML traduce el badge de clave del SP y las acciones", async () => {
    renderIn("en", <SamlConfigPage />, { saml: true });

    expect((await screen.findByTestId("saml-enabled-badge")).textContent).toContain("inactive");
    expect(screen.getByTestId("saml-key-badge").textContent).toContain("SP key: encrypted at rest");
    expect(screen.getByTestId("saml-toggle-enabled").textContent).toContain("Enable");

    expect(screen.getByTestId("saml-enabled-badge").textContent).not.toContain("inactivo");
  });

  it("el diálogo SAML se traduce entero, incluidos NameID y los flags de seguridad", async () => {
    renderIn("en", <SamlConfigPage />);

    fireEvent.click(await screen.findByTestId("saml-create-button"));

    expect(await screen.findByText("IdP metadata (XML)")).toBeDefined();
    expect(screen.getByTestId("saml-form-metadata-upload").textContent).toContain("Upload XML");
    expect(screen.getByTestId("saml-form-metadata-parse").textContent).toContain("Extract data");
    expect(screen.getByText("NameID format")).toBeDefined();
    expect(screen.getByText("emailAddress (recommended)")).toBeDefined();
    expect(screen.getByText("Require assertions signed by the IdP (recommended)")).toBeDefined();
    expect(screen.getByTestId("saml-form-submit").textContent).toContain("Create");

    expect(screen.queryByText("Metadatos del IdP (XML)")).toBeNull();
    expect(screen.queryByText("emailAddress (recomendado)")).toBeNull();
    expect(screen.queryByText("Formato de NameID")).toBeNull();
  });

  it("el placeholder del XML del IdP también cambia de idioma", async () => {
    renderIn("es", <SamlConfigPage />);
    fireEvent.click(await screen.findByTestId("saml-create-button"));
    await waitFor(() =>
      expect(screen.getByTestId("saml-form-metadata").getAttribute("placeholder")).toContain(
        "Pega aquí el EntityDescriptor",
      ),
    );

    cleanup();

    renderIn("en", <SamlConfigPage />);
    fireEvent.click(await screen.findByTestId("saml-create-button"));
    await waitFor(() =>
      expect(screen.getByTestId("saml-form-metadata").getAttribute("placeholder")).toContain(
        "Paste the IdP EntityDescriptor here",
      ),
    );
  });
});
