// @vitest-environment jsdom
/**
 * Diálogo «Personalizar (crear copia)» — el fork de un agente.
 *
 * Existe por el defecto que dejó la migración 0126: hay un índice único parcial
 * `(tenant_id, project_id, name)` sobre los agentes vivos, el backend hereda el
 * nombre del origen cuando no se le da uno, y **forkear dos veces al mismo
 * proyecto** chocaba con el índice. Se decidió que la API devuelva 409 y NO
 * auto-renombre (el nombre es identidad: por él se eligen agentes en los
 * `role_map`), así que la parte de la UI son estas dos cosas, y las dos se fijan
 * aquí:
 *
 *  - **sugerir un nombre libre en el destino**, visible y editable — la
 *    sugerencia va delante del usuario, no detrás;
 *  - **explicar el 409** en vez de pintar un fallo genérico (o, como hacía este
 *    diálogo, el `message` crudo de `ApiError`, que es `api 409: {…}`).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

import { AgentForkDialog } from "@/app/admin/agents/[id]/agent-fork-dialog";
import type { Agent } from "@/app/admin/agents/[id]/agent-detail-types";
import { ApiError } from "@/lib/api";
import { translate } from "@/lib/i18n/translate";

const SOURCE = {
  id: "src-1",
  tenant_id: "t1",
  name: "Ada",
  description: null,
  agent_type: "ai",
  role: "backend_dev",
  system_prompt: "",
  memory_scope: "project_shared",
  review_capability: false,
  max_concurrent_tasks: 1,
  is_template: true,
  scope: "tenant",
  project_id: null,
  forked_from_agent_id: null,
  teams: [],
} satisfies Agent;

const PROJECTS = [
  { id: "p1", name: "Proyecto Uno", is_template: false },
  { id: "p2", name: "Proyecto Dos", is_template: false },
  { id: "p3", name: "Plantilla", is_template: true },
];

/** `p1` YA tiene una copia de Ada; `p2` no. Es el caso del segundo fork. */
const AGENTS = [
  { id: "a1", name: "Ada", project_id: null },
  { id: "a2", name: "Ada (copia)", project_id: "p1" },
  { id: "a3", name: "Linus", project_id: "p2" },
];

/** Error que devuelve el fork cuando falla; `null` = crea bien. */
let forkError: unknown = null;

function routeApi(path: string): unknown {
  if (path === "/projects") return PROJECTS;
  if (path === "/agents") return AGENTS;
  if (path === "/agents/src-1/fork") {
    if (forkError) throw forkError;
    return { ...SOURCE, id: "fork-1" };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

const onForked = vi.fn();

function renderDialog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AgentForkDialog agent={SOURCE} open onOpenChange={() => {}} onForked={onForked} />
    </QueryClientProvider>,
  );
}

/**
 * Elige el proyecto destino.
 *
 * Espera a que la opción EXISTA antes de disparar el `change`: el `<select>` se
 * pinta desde el primer render, con sólo el placeholder, y cambiarlo a un valor
 * sin opción es un no-op silencioso —jsdom lo deja en `""`— que convierte el
 * test en un flaky que depende de si la consulta de proyectos ya respondió.
 */
async function pickProject(value: string, label: string) {
  const select = (await screen.findByTestId("fork-agent-project")) as HTMLSelectElement;
  await screen.findByRole("option", { name: label });
  fireEvent.change(select, { target: { value } });
  expect(select.value).toBe(value);
}

/** [ruta, init] de las llamadas de escritura (las que llevan `init`). */
function writes(): [string, { method?: string; body?: Record<string, unknown> }][] {
  return apiFetchMock.mock.calls.filter(([, init]) => init !== undefined) as [
    string,
    { method?: string; body?: Record<string, unknown> },
  ][];
}

beforeEach(() => {
  forkError = null;
  onForked.mockReset();
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation(async (path: string) => routeApi(path));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("nombre sugerido para la copia", () => {
  it("arranca con la copia llana y la manda en el cuerpo", async () => {
    renderDialog();

    await waitFor(() =>
      expect(screen.getByTestId("fork-agent-name")).toHaveProperty("value", "Ada (copia)"),
    );

    await pickProject("p2", "Proyecto Dos");
    fireEvent.click(screen.getByTestId("fork-agent-submit"));

    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0][0]).toBe("/agents/src-1/fork");
    expect(writes()[0][1].body).toEqual({ project_id: "p2", name: "Ada (copia)" });
  });

  it("al elegir un destino que ya tiene esa copia, sugiere la siguiente libre", async () => {
    renderDialog();

    await pickProject("p1", "Proyecto Uno");

    // `p1` ya tiene «Ada (copia)»: insistir con ese nombre es el 500 que se
    // arregla en el backend. La sugerencia lo esquiva ANTES de enviar.
    await waitFor(() =>
      expect(screen.getByTestId("fork-agent-name")).toHaveProperty("value", "Ada (copia 2)"),
    );

    fireEvent.click(screen.getByTestId("fork-agent-submit"));
    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0][1].body).toEqual({ project_id: "p1", name: "Ada (copia 2)" });
  });

  it("lo que escriba el usuario manda sobre la sugerencia", async () => {
    renderDialog();
    await screen.findByTestId("fork-agent-name");

    fireEvent.change(screen.getByTestId("fork-agent-name"), { target: { value: "Ada Junior" } });
    // Cambiar de destino después NO debe pisar lo tecleado.
    await pickProject("p1", "Proyecto Uno");
    expect(screen.getByTestId("fork-agent-name")).toHaveProperty("value", "Ada Junior");

    fireEvent.click(screen.getByTestId("fork-agent-submit"));
    await waitFor(() => expect(writes().length).toBe(1));
    expect(writes()[0][1].body).toEqual({ project_id: "p1", name: "Ada Junior" });
  });
});

describe("colisión de nombre (409)", () => {
  it("explica el choque en vez de enseñar el error crudo", async () => {
    forkError = new ApiError(409, JSON.stringify({ detail: "agent name already in use" }));
    renderDialog();
    await screen.findByTestId("fork-agent-name");

    await pickProject("p2", "Proyecto Dos");
    fireEvent.click(screen.getByTestId("fork-agent-submit"));

    const shown = await screen.findByTestId("fork-agent-error");
    expect(shown.textContent).toBe(
      translate("es", "agents", "forkConflictName", { name: "Ada (copia)" }),
    );
    // El cuerpo crudo del backend no sale a pantalla (prod-16 `task_prod16_05`).
    expect(shown.textContent).not.toContain("api 409");
  });

  it("un error que NO es de nombre sigue saliendo traducido, no en crudo", async () => {
    forkError = new ApiError(500, "<html>nginx</html>");
    renderDialog();
    await screen.findByTestId("fork-agent-name");

    await pickProject("p2", "Proyecto Dos");
    fireEvent.click(screen.getByTestId("fork-agent-submit"));

    const shown = await screen.findByTestId("fork-agent-error");
    expect(shown.textContent).toBe(translate("es", "errors", "server"));
  });
});
