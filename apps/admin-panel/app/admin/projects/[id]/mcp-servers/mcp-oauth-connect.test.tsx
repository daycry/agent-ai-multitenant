// @vitest-environment jsdom
// ADR 0127 — botón «Conectar» del flujo OAuth (`McpOAuthConnect`). Clava:
//   - refleja el estado: "Conectado" (+ Reconectar) vs "No conectado" (+ Conectar);
//   - pulsar «Conectar» hace POST al endpoint /oauth/connect y redirige a la
//     `authorization_url` devuelta (vía onAuthorize inyectable);
//   - un error del connect se muestra con gracia (no rompe);
//   - si el endpoint de estado no está disponible, muestra el aviso pero deja Conectar.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { McpOAuthConnect } from "@/app/admin/projects/[id]/mcp-servers/mcp-oauth-connect";

const PROJECT = "proj-1";
const SERVER = "atlassian-remote";

function renderConnect(opts: {
  status?: { connected: boolean; expires_at: string | null } | Error;
  connect?: { authorization_url: string } | Error;
  onAuthorize?: (url: string) => void;
}) {
  apiFetchMock.mockImplementation((path: string, o?: { method?: string }) => {
    if (path.endsWith("/oauth/status")) {
      return opts.status instanceof Error
        ? Promise.reject(opts.status)
        : Promise.resolve(opts.status ?? { connected: false, expires_at: null });
    }
    if (path.endsWith("/oauth/connect") && o?.method === "POST") {
      return opts.connect instanceof Error
        ? Promise.reject(opts.connect)
        : Promise.resolve(opts.connect ?? { authorization_url: "https://auth.example/x" });
    }
    return Promise.reject(new Error(`unexpected path ${path}`));
  });

  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <McpOAuthConnect
        projectId={PROJECT}
        serverName={SERVER}
        providerLabel="Atlassian"
        onAuthorize={opts.onAuthorize}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("McpOAuthConnect", () => {
  it("muestra 'No conectado' y el botón Conectar cuando no hay token", async () => {
    renderConnect({ status: { connected: false, expires_at: null } });
    await waitFor(() =>
      expect(screen.getByTestId(`mcp-oauth-status-disconnected-${SERVER}`)).toBeTruthy(),
    );
    const btn = screen.getByTestId(`mcp-oauth-connect-button-${SERVER}`);
    expect(btn.textContent).toContain("Conectar");
  });

  it("muestra 'Conectado' y Reconectar cuando hay token válido", async () => {
    renderConnect({ status: { connected: true, expires_at: "2026-07-24T10:00:00Z" } });
    await waitFor(() =>
      expect(screen.getByTestId(`mcp-oauth-status-connected-${SERVER}`)).toBeTruthy(),
    );
    expect(screen.getByTestId(`mcp-oauth-connect-button-${SERVER}`).textContent).toContain(
      "Reconectar",
    );
    expect(screen.getByTestId(`mcp-oauth-expires-${SERVER}`)).toBeTruthy();
  });

  it("Conectar hace POST y redirige a la authorization_url devuelta", async () => {
    const onAuthorize = vi.fn();
    renderConnect({
      status: { connected: false, expires_at: null },
      connect: { authorization_url: "https://mcp.atlassian.com/v1/authorize?x=1" },
      onAuthorize,
    });
    await waitFor(() =>
      expect(screen.getByTestId(`mcp-oauth-connect-button-${SERVER}`)).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId(`mcp-oauth-connect-button-${SERVER}`));
    await waitFor(() =>
      expect(onAuthorize).toHaveBeenCalledWith("https://mcp.atlassian.com/v1/authorize?x=1"),
    );
    // el POST fue al endpoint /oauth/connect
    expect(
      apiFetchMock.mock.calls.some(
        ([p, o]) =>
          String(p).endsWith(`/mcp-servers/${SERVER}/oauth/connect`) &&
          (o as { method?: string } | undefined)?.method === "POST",
      ),
    ).toBe(true);
  });

  it("muestra el error del connect con gracia (no redirige)", async () => {
    const onAuthorize = vi.fn();
    renderConnect({
      status: { connected: false, expires_at: null },
      connect: new Error("backend OAuth no disponible"),
      onAuthorize,
    });
    await waitFor(() =>
      expect(screen.getByTestId(`mcp-oauth-connect-button-${SERVER}`)).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId(`mcp-oauth-connect-button-${SERVER}`));
    await waitFor(() =>
      expect(screen.getByTestId(`mcp-oauth-connect-error-${SERVER}`).textContent).toContain(
        "backend OAuth no disponible",
      ),
    );
    expect(onAuthorize).not.toHaveBeenCalled();
  });

  it("si el estado no está disponible, avisa pero deja Conectar", async () => {
    renderConnect({ status: new Error("404") });
    await waitFor(() =>
      expect(screen.getByTestId(`mcp-oauth-status-unavailable-${SERVER}`)).toBeTruthy(),
    );
    // sigue ofreciendo el botón (tratado como desconectado)
    expect(screen.getByTestId(`mcp-oauth-connect-button-${SERVER}`).textContent).toContain(
      "Conectar",
    );
  });
});
