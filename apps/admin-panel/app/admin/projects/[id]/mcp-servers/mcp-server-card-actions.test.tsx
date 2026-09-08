// @vitest-environment jsdom
// ADR 0166 D2/D4 (task_mk_01): la tarjeta del servidor enseña el hueco y lo cierra
// en un viaje.
//
//   - «sin importar» cuando el catálogo no tiene filas `<server>.*`; «N tools
//     importadas» cuando las tiene; nada mientras el catálogo carga;
//   - «Importar» hace POST a import-tools SIN `tool_names` (todas las anunciadas)
//     y pinta cuántas entraron y cuántas se retiraron;
//   - «Probar» hace POST a test-connection con el servidor tal como está guardado;
//   - los dos códigos tipados del ADR (`TOO_MANY_TOOLS`, `OAUTH_NOT_CONNECTED`)
//     se traducen a una instrucción, no al texto crudo.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { McpServerCardActions } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-card-actions";
import { type McpServerConfig } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";
import { ApiError } from "@/lib/api";

const SERVER: McpServerConfig = {
  name: "gh",
  transport: "streamable_http",
  command: null,
  args: [],
  env: {},
  url: "https://api.githubcopilot.com/mcp/",
  headers: {},
  auth_ref: null,
  timeout_s: 30,
};

function mount(importedCount: number | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <McpServerCardActions
        projectId="p1"
        server={SERVER}
        importedCount={importedCount}
        disabled={false}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("McpServerCardActions", () => {
  it("con cero filas dice «sin importar»; con N, cuántas; cargando, nada", () => {
    mount(0);
    expect(screen.getByTestId("mcp-server-not-imported-gh").textContent).toBe("sin importar");
    cleanup();
    mount(12);
    expect(screen.getByTestId("mcp-server-imported-gh").textContent).toBe("12 tools importadas");
    cleanup();
    mount(null);
    expect(screen.queryByTestId("mcp-server-not-imported-gh")).toBeNull();
    expect(screen.queryByTestId("mcp-server-imported-gh")).toBeNull();
  });

  it("«Importar» pide todas las anunciadas (sin tool_names) y pinta el resultado", async () => {
    apiFetchMock.mockResolvedValue({
      tools: [{ name: "gh.a" }, { name: "gh.b" }],
      retired: ["gh.old"],
      omitted: [],
      warnings: ["la tool 'x' se omite: pasa de 32 KiB"],
    });
    mount(0);
    fireEvent.click(screen.getByTestId("mcp-server-import-gh"));
    await waitFor(() => expect(screen.getByTestId("mcp-server-import-result-gh")).toBeTruthy());
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/projects/p1/mcp/servers/gh/import-tools",
      expect.objectContaining({ method: "POST", body: {} }),
    );
    const result = screen.getByTestId("mcp-server-import-result-gh").textContent ?? "";
    expect(result).toContain("Importadas 2 tools");
    expect(result).toContain("1 retiradas");
    expect(result).toContain("32 KiB");
  });

  it("«Probar» manda el servidor guardado y dice cuántas tools anuncia", async () => {
    apiFetchMock.mockResolvedValue({
      server_name: "gh",
      tools: [{ name: "a" }, { name: "b" }, { name: "c" }],
    });
    mount(0);
    fireEvent.click(screen.getByTestId("mcp-server-test-gh"));
    await waitFor(() => expect(screen.getByTestId("mcp-server-test-ok-gh")).toBeTruthy());
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/projects/p1/mcp/test-connection",
      expect.objectContaining({ method: "POST", body: SERVER }),
    );
    expect(screen.getByTestId("mcp-server-test-ok-gh").textContent).toContain("3 tools");
  });

  it("TOO_MANY_TOOLS y OAUTH_NOT_CONNECTED se traducen a una instrucción", async () => {
    apiFetchMock.mockRejectedValue(
      new ApiError(
        422,
        JSON.stringify({ detail: { error_code: "TOO_MANY_TOOLS", message: "431" } }),
      ),
    );
    mount(0);
    fireEvent.click(screen.getByTestId("mcp-server-import-gh"));
    await waitFor(() => expect(screen.getByTestId("mcp-server-import-error-gh")).toBeTruthy());
    expect(screen.getByTestId("mcp-server-import-error-gh").textContent).toContain("más de 200");
    cleanup();

    apiFetchMock.mockRejectedValue(
      new ApiError(
        401,
        JSON.stringify({ detail: { error_code: "OAUTH_NOT_CONNECTED", message: "x" } }),
      ),
    );
    mount(0);
    fireEvent.click(screen.getByTestId("mcp-server-import-gh"));
    await waitFor(() => expect(screen.getByTestId("mcp-server-import-error-gh")).toBeTruthy());
    expect(screen.getByTestId("mcp-server-import-error-gh").textContent).toContain("Conectar");
  });
});
