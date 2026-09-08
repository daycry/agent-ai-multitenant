// @vitest-environment jsdom
// task_mk_02 (ADR 0165 D9.2): «Probar conexión» ramifica por el `error_code`
// tipado del backend, no por el texto.
//
// El caso que motiva el ADR: un `403 Filtered` del egress-proxy llegaba como
// `TRANSPORT_ERROR` con el texto crudo, y el operador lo leía como un 403 del
// servidor MCP y rotaba un token sano. Ahora:
//   - `EGRESS_BLOCKED` ⇒ nota accionable (pedir la apertura a un System Admin)
//     ENCIMA del mensaje del backend, que trae el host y el runbook;
//   - `EGRESS_PROXY_UNAVAILABLE` ⇒ otra nota: no es la allowlist;
//   - cualquier otro código ⇒ sólo el mensaje, como antes.
// Y `mcpErrorCode` no se rompe con cuerpos que no son JSON ni con errores que no
// son de la API.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { McpConnectionTestSection } from "@/app/admin/projects/[id]/mcp-servers/mcp-connection-test-section";
import {
  mcpErrorCode,
  type McpServerConfig,
} from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";
import { ApiError } from "@/lib/api";

const REMOTE: McpServerConfig = {
  name: "atlassian",
  transport: "streamable_http",
  command: null,
  args: [],
  env: {},
  url: "https://mcp.atlassian.com/v1/mcp",
  headers: {},
  auth_ref: null,
  timeout_s: 30,
};

function mount() {
  render(<McpConnectionTestSection projectId="p1" buildPayload={() => REMOTE} disabled={false} />);
}

function failWith(status: number, errorCode: string, message: string) {
  apiFetchMock.mockRejectedValue(
    new ApiError(status, JSON.stringify({ detail: { error_code: errorCode, message } })),
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("mcpErrorCode", () => {
  it("lee el error_code tipado del cuerpo", () => {
    const err = new ApiError(422, '{"detail":{"error_code":"EGRESS_BLOCKED","message":"x"}}');
    expect(mcpErrorCode(err)).toBe("EGRESS_BLOCKED");
  });

  it("devuelve null con un cuerpo que no es JSON, un detail plano o un error ajeno", () => {
    expect(mcpErrorCode(new ApiError(502, "<html>Bad Gateway</html>"))).toBeNull();
    expect(mcpErrorCode(new ApiError(404, '{"detail":"project not found"}'))).toBeNull();
    expect(mcpErrorCode(new TypeError("Failed to fetch"))).toBeNull();
  });
});

describe("McpConnectionTestSection — errores de egress", () => {
  it("EGRESS_BLOCKED: nota accionable + el mensaje del backend con el host", async () => {
    failWith(422, "EGRESS_BLOCKED", "`mcp.atlassian.com` no está en la allowlist (runbook).");
    mount();
    fireEvent.click(screen.getByTestId("mcp-form-test"));
    await waitFor(() => expect(screen.getByTestId("mcp-form-test-egress-blocked")).toBeTruthy());
    expect(screen.getByTestId("mcp-form-test-egress-blocked").textContent).toContain(
      "System Admin",
    );
    expect(screen.getByTestId("mcp-form-test-error").textContent).toContain("mcp.atlassian.com");
    expect(screen.queryByTestId("mcp-form-test-egress-proxy-unavailable")).toBeNull();
  });

  it("EGRESS_PROXY_UNAVAILABLE: la nota dice que NO es la allowlist", async () => {
    failWith(502, "EGRESS_PROXY_UNAVAILABLE", "No se pudo conectar con el egress-proxy.");
    mount();
    fireEvent.click(screen.getByTestId("mcp-form-test"));
    await waitFor(() =>
      expect(screen.getByTestId("mcp-form-test-egress-proxy-unavailable")).toBeTruthy(),
    );
    expect(screen.getByTestId("mcp-form-test-egress-proxy-unavailable").textContent).toContain(
      "No es la allowlist",
    );
    expect(screen.queryByTestId("mcp-form-test-egress-blocked")).toBeNull();
  });

  it("otro código: sólo el mensaje, sin nota de egress", async () => {
    failWith(502, "TRANSPORT_ERROR", "connection refused");
    mount();
    fireEvent.click(screen.getByTestId("mcp-form-test"));
    await waitFor(() => expect(screen.getByTestId("mcp-form-test-error")).toBeTruthy());
    expect(screen.getByTestId("mcp-form-test-error").textContent).toContain("connection refused");
    expect(screen.queryByTestId("mcp-form-test-egress-blocked")).toBeNull();
    expect(screen.queryByTestId("mcp-form-test-egress-proxy-unavailable")).toBeNull();
  });

  it("un cuerpo que no es JSON no rompe la pantalla ni se pinta crudo", async () => {
    apiFetchMock.mockRejectedValue(new ApiError(502, "<html>Bad Gateway</html>"));
    mount();
    fireEvent.click(screen.getByTestId("mcp-form-test"));
    await waitFor(() => expect(screen.getByTestId("mcp-form-test-error")).toBeTruthy());
    expect(screen.getByTestId("mcp-form-test-error").textContent).not.toContain("<html>");
  });
});
