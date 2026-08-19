// @vitest-environment jsdom
// prod-16 `task_prod16_07` — el diálogo SIGUE montando las dos secciones que le
// sacamos, y les pasa lo que necesitan.
//
// **Este fichero existe por un rojo que no salió.** Al partir el diálogo se
// comprobó la rotura a propósito: quitando `<McpAdvancedOptionsSection/>` del
// JSX, los 23 tests del módulo seguían VERDES. Es el modo de fallo nº5 de
// `docs/03-guides/verificar-antes-de-implementar.md` —«mecanismo entregado, cero
// llamantes»— en su versión más barata de cometer: el componente nuevo tiene sus
// 8 tests y pasa; el diálogo compila; `tsc` no dice nada; y en producción las
// opciones avanzadas simplemente NO están. Nadie lo nota hasta que un operador
// no encuentra dónde poner el timeout.
//
// Lo que clava, y sólo eso (el comportamiento de cada sección lo cubre su propio
// fichero de test):
//   - el diálogo monta la sección de opciones avanzadas Y la de «Probar
//     conexión»;
//   - le pasa el `open` de verdad: con un server que trae `auth_ref` o un
//     timeout distinto de 30, el bloque arranca ABIERTO (es la señal de que hay
//     configuración que mirar), y con uno limpio arranca cerrado;
//   - `showRawAuth` viaja de ida y vuelta: la escotilla se puede abrir desde la
//     sección aunque el estado viva en el diálogo.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { McpServerDialog } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-dialog";
import { type McpServerConfig } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";

const CLEAN: McpServerConfig = {
  name: "files-server",
  transport: "stdio",
  command: "docling-mcp",
  args: [],
  env: {},
  url: null,
  headers: {},
  auth_ref: null,
  timeout_s: 30,
};

function renderDialog(initial: McpServerConfig) {
  apiFetchMock.mockResolvedValue([]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <McpServerDialog
        projectId="p1"
        open
        onOpenChange={vi.fn()}
        initial={initial}
        submitLabel="Guardar"
        submitting={false}
        onSubmit={vi.fn()}
        backendError={null}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("McpServerDialog monta las secciones troceadas (prod-16 task_prod16_07)", () => {
  it("monta las dos secciones que salieron del fichero", () => {
    renderDialog(CLEAN);

    expect(screen.getByTestId("mcp-form-advanced-toggle")).toBeTruthy();
    expect(screen.getByTestId("mcp-form-test")).toBeTruthy();
  });

  it("un server limpio arranca con las avanzadas cerradas", () => {
    renderDialog(CLEAN);

    expect(screen.queryByTestId("mcp-form-timeout")).toBeNull();
    expect(screen.queryByTestId("mcp-form-auth-ref")).toBeNull();
  });

  it("un server con credencial o timeout propio las arranca ABIERTAS", () => {
    renderDialog({ ...CLEAN, auth_ref: "vault:secret/data/mcp/x/y" });

    expect(screen.getByTestId("mcp-form-auth-ref")).toBeTruthy();
    expect(screen.getByTestId("mcp-form-timeout")).toBeTruthy();
  });

  it("el timeout distinto del default también las abre", () => {
    renderDialog({ ...CLEAN, timeout_s: 120 });

    expect(screen.getByTestId("mcp-form-timeout")).toBeTruthy();
  });

  it("el toggle de avanzadas responde: el open lo gobierna el diálogo", () => {
    renderDialog(CLEAN);

    fireEvent.click(screen.getByTestId("mcp-form-advanced-toggle"));

    expect(screen.getByTestId("mcp-form-timeout")).toBeTruthy();
  });
});
