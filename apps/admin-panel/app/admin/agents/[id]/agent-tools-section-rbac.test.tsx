// @vitest-environment jsdom
// Plan 06.15 (agent-tools-assignment-ui), test humano de RBAC: «como tenant_user
// la sección de tools es de SOLO LECTURA».
//
// El vitest hermano (`agent-tools-section.test.tsx`) mockea `isTenantAdmin: true`
// y por tanto nunca ejercita esa mitad: `canEdit = !isReadOnly && isTenantAdmin`
// podría degenerar a `!isReadOnly` sin que ningún test se enterara — y entonces
// un tenant_user vería «Guardar» y checkboxes activos para acabar chocando con un
// 403 del backend. Este fichero fija el caso `isTenantAdmin: false`.
//
// Va en un fichero aparte a propósito: `vi.mock` es de ámbito de FICHERO, así que
// no se puede tener el mismo hook mockeado con dos valores en el mismo archivo.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// El caso que faltaba: miembro del tenant SIN rol admin.
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isSystemOwner: false,
    isTenantAdmin: false,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import { LanguageProvider } from "@/lib/lang-context";
import { AgentToolsSection } from "@/app/admin/agents/[id]/agent-tools-section";

const CATALOG = [
  {
    id: "b1",
    name: "read_file",
    description: "Lee un fichero",
    category: "file",
    implementation_type: "builtin",
    security_level: "safe",
    is_builtin: true,
    is_runtime_wired: true,
  },
  {
    id: "b2",
    name: "write_file",
    description: "Escribe un fichero",
    category: "file",
    implementation_type: "builtin",
    security_level: "privileged",
    is_builtin: true,
    is_runtime_wired: true,
  },
  {
    id: "c1",
    name: "my_webhook",
    description: "Llama a un webhook",
    category: "custom",
    implementation_type: "http_endpoint",
    security_level: "safe",
    is_builtin: false,
    is_runtime_wired: true,
  },
];

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/tools?limit=500") return Promise.resolve(CATALOG);
    if (path === "/agents/agent-1/tools") {
      return Promise.resolve([{ tool_id: "b1", tool_name: "read_file" }]);
    }
    return Promise.resolve([]);
  });
}

function mount({ isReadOnly = false } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <AgentToolsSection agentId="agent-1" isReadOnly={isReadOnly} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

// Los `waitFor` de este fichero esperan transiciones de TanStack Query. El
// timeout por defecto de RTL (1s) se queda corto cuando la suite corre entera en
// paralelo y la máquina va cargada: se vio un rojo fantasma así. Se sube aquí
// (por fichero) en vez de tocar la config compartida.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("AgentToolsSection — tenant_user: solo lectura (06.15)", () => {
  it("no ofrece guardar ni descartar aunque isReadOnly sea false", async () => {
    wireApi();
    mount({ isReadOnly: false });
    await waitFor(() => expect(screen.getByTestId("agent-tool-row-b1")).toBeTruthy());
    // canEdit = !isReadOnly && isTenantAdmin → false por el ROL, no por el prop.
    expect(screen.queryByTestId("agent-tools-save")).toBeNull();
    expect(screen.queryByTestId("agent-tools-reset")).toBeNull();
  });

  it("pinta el catálogo pero con TODOS los checkboxes deshabilitados", async () => {
    wireApi();
    mount({ isReadOnly: false });
    // Se espera al ESTADO que este test afirma, no a que el checkbox exista.
    //
    // Son DOS queries: `["tools-catalog"]` pinta la fila y `["agent-tools", id]`
    // decide el `checked`. Esperar sólo a la existencia deja pasar el instante en
    // que el catálogo ya resolvió y las asignaciones no, y en ese instante
    // `b1.checked` es false — un rojo que la máquina rápida no ve nunca y el
    // runner cargado sí (run 32473901482 en master, 1 de 1423).
    //
    // El `asyncUtilTimeout` de arriba se subió por este mismo rojo y no podía
    // arreglarlo: `waitFor` se satisfacía al instante con la existencia, así que
    // el reloj nunca fue la restricción.
    await waitFor(() =>
      expect((screen.getByTestId("agent-tool-checkbox-b1") as HTMLInputElement).checked).toBe(true),
    );

    // La lista se VE (es una vista de consulta legítima)…
    const boxes = ["b1", "b2"].map(
      (id) => screen.getByTestId(`agent-tool-checkbox-${id}`) as HTMLInputElement,
    );
    // …y la guarda encontró algo: si el catálogo dejara de pintar filas, el bucle
    // pasaría en vacío y este test no protegería nada.
    expect(boxes).toHaveLength(2);
    for (const box of boxes) expect(box.disabled).toBe(true);

    // El estado asignado se sigue reflejando (b1 está concedida).
    expect(boxes[0].checked).toBe(true);
    expect(boxes[1].checked).toBe(false);
  });

  it("no ofrece los toggles masivos por categoría", async () => {
    wireApi();
    mount({ isReadOnly: false });
    await waitFor(() => expect(screen.getByTestId("agent-tools-group-file")).toBeTruthy());
    expect(screen.queryByTestId("agent-tools-group-toggle-file")).toBeNull();
  });

  it("nunca hace el PUT de asignación (ni siquiera al montar)", async () => {
    wireApi();
    mount({ isReadOnly: false });
    await waitFor(() => expect(screen.getByTestId("agent-tool-row-b1")).toBeTruthy());
    const writes = apiFetchMock.mock.calls.filter(
      ([, opts]) => (opts as { method?: string } | undefined)?.method === "PUT",
    );
    expect(writes).toHaveLength(0);
  });
});
