// @vitest-environment jsdom
// Plan 08 `task_08_03` — la callback OIDC que el operador registra en el IdP.
//
// El test humano decía "la pantalla muestra la callback a registrar en el IdP
// y se puede copiar". El botón existía (`data-testid="sso-callback-copy"`) y
// NADIE afirmaba el copiado: `grep clipboard` en los tests daba 0. Un botón
// "Copiar" que no escribe en el portapapeles es indistinguible de uno que sí
// lo hace hasta que un humano lo prueba, que es justo lo que queremos dejar de
// depender.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

// El gating de rol de la tarjeta no es lo que se prueba aquí: un tenant_admin
// (no system admin) ve la base pública en modo lectura y la callback + copiar.
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

import SsoConfigPage from "@/app/admin/settings/sso/page";

const CALLBACK_URL = "https://agentic.example.com/auth/sso/oidc/callback";

const writeTextMock = vi.fn<(text: string) => Promise<void>>();

/** Respuestas por endpoint (la pantalla lanza 4 GET al montar). */
function routeApi(path: string): unknown {
  if (path === "/auth/sso/config") return [];
  if (path === "/auth/sso/oidc/callback-url") return { callback_url: CALLBACK_URL };
  if (path === "/auth/sso/public-base-url") {
    return {
      base_url: "https://agentic.example.com",
      is_override: true,
      env_default: "http://localhost:8001",
    };
  }
  if (path === "/auth/sso/api-path-prefix") {
    return { prefix: "", is_override: true, env_default: "" };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SsoConfigPage />
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

describe("callback OIDC a registrar en el IdP", () => {
  it("muestra la URL que devuelve el backend", async () => {
    renderPage();
    const code = await screen.findByTestId("sso-callback-url");
    await waitFor(() => expect(code.textContent).toBe(CALLBACK_URL));
    expect(apiFetchMock).toHaveBeenCalledWith("/auth/sso/oidc/callback-url", undefined);
  });

  it("el botón de copiar escribe ESA url en el portapapeles y confirma", async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("sso-callback-url").textContent).toBe(CALLBACK_URL),
    );

    const button = await screen.findByTestId("sso-callback-copy");
    expect(button.textContent).toContain("Copiar");

    fireEvent.click(button);

    await waitFor(() => expect(writeTextMock).toHaveBeenCalledTimes(1));
    expect(writeTextMock).toHaveBeenCalledWith(CALLBACK_URL);
    // Feedback visible: el botón pasa a "Copiado".
    await waitFor(() =>
      expect(screen.getByTestId("sso-callback-copy").textContent).toContain("Copiado"),
    );
  });

  it("mientras no hay URL el botón está deshabilitado y no copia nada", async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      if (path === "/auth/sso/oidc/callback-url") return new Promise(() => {}); // nunca resuelve
      return routeApi(path);
    });
    renderPage();

    const button = await screen.findByTestId("sso-callback-copy");
    expect(button).toHaveProperty("disabled", true);
    fireEvent.click(button);
    expect(writeTextMock).not.toHaveBeenCalled();
  });

  it("si el portapapeles no está disponible, no revienta la pantalla", async () => {
    writeTextMock.mockRejectedValue(new Error("insecure context"));
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("sso-callback-url").textContent).toBe(CALLBACK_URL),
    );

    fireEvent.click(screen.getByTestId("sso-callback-copy"));

    await waitFor(() => expect(writeTextMock).toHaveBeenCalledTimes(1));
    // Sigue en pie y NO miente diciendo "Copiado".
    expect(screen.getByTestId("sso-callback-card")).not.toBeNull();
    expect(screen.getByTestId("sso-callback-copy").textContent).toContain("Copiar");
    expect(screen.getByTestId("sso-callback-copy").textContent).not.toContain("Copiado");
  });

  it("avisa si la base pública sigue siendo el valor de arranque", async () => {
    apiFetchMock.mockImplementation(async (path: string) => {
      if (path === "/auth/sso/public-base-url") {
        return {
          base_url: "http://localhost:8001",
          is_override: false,
          env_default: "http://localhost:8001",
        };
      }
      return routeApi(path);
    });
    renderPage();

    const warning = await screen.findByTestId("sso-redirect-base-warning");
    expect(warning.textContent).toContain("valor de arranque");
  });
});
