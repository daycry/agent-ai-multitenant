// @vitest-environment jsdom

/**
 * `app/admin/plans/[id]/escalated` — el panel de tareas escaladas de un plan,
 * en los dos idiomas (plan prod-16, `task_prod16_03`).
 *
 * La `ATTR_ALLOWLIST` le veía **2 atributos** en 346 líneas. Lo que se lee es
 * todo lo demás: la miga de pan, la cabecera, los dos botones de acción, los
 * tres estados de la lista, el contador de reintentos, el desplegable del
 * historial y el diálogo de «tarea libre» completo.
 *
 * Es una pantalla de DECISIÓN —quien la abre está desatascando trabajo que un
 * revisor automático rechazó tres veces— así que leerla a medias en otro idioma
 * es el peor sitio donde dejar la deuda. Las cinco acciones humanas ya estaban
 * bilingües (`components/tasks/task-human-actions.tsx`, migrado con el lote de
 * `tasks/*`): lo que faltaba era el marco que las rodea.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "plan-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/plans/plan-1/escalated",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import EscalatedPage from "@/app/admin/plans/[id]/escalated/page";

const STORAGE_KEY = "admin-panel.lang";

const PLAN = { id: "plan-1", project_id: "proj-1", title: "Plan CI4", status: "blocked" };

const TASK = {
  id: "task-1",
  title: "Implementar el contrato",
  description: "Tres intentos fallidos",
  retry_count: 3,
  history: [{ at: 1_760_000_000, kind: "review_rejected", payload: {} }],
};

function wire({ plan = PLAN, tasks = [TASK] }: { plan?: unknown; tasks?: unknown[] } = {}) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/plans/plan-1") return Promise.resolve(plan);
    if (path === "/plans/plan-1/escalated-tasks") return Promise.resolve({ tasks });
    return Promise.resolve({});
  });
}

function mount(lang: "es" | "en") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <EscalatedPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("tareas escaladas en los dos idiomas", () => {
  it("rinde miga de pan, cabecera y fila en castellano", async () => {
    wire();
    mount("es");

    expect(await screen.findByText("Tareas escaladas")).toBeDefined();
    expect(screen.getByText("Proyectos")).toBeDefined();
    expect((await screen.findByTestId("escalated-task-1-retries")).textContent).toBe(
      "3 reintentos",
    );
    expect(screen.getByTestId("free-task-open").textContent).toContain("Añadir tarea libre");
  });

  it("traduce miga de pan, cabecera y los dos botones de acción", async () => {
    wire();
    mount("en");

    expect(await screen.findByText("Escalated tasks")).toBeDefined();
    expect(screen.getByText("Projects")).toBeDefined();
    expect(screen.getByText(/reached the automatic reviewer's retry limit/)).toBeDefined();
    expect(screen.getByTestId("plan-unblock").textContent).toContain("Unblock plan");
    expect(screen.getByTestId("free-task-open").textContent).toContain("Add a free task");

    expect(screen.queryByText("Tareas escaladas")).toBeNull();
    expect(screen.queryByText("Desbloquear plan")).toBeNull();
  });

  it("traduce el contador de reintentos en singular y en plural", async () => {
    wire({
      tasks: [
        { ...TASK, retry_count: 1 },
        { ...TASK, id: "task-2", retry_count: 4 },
      ],
    });
    mount("en");

    expect((await screen.findByTestId("escalated-task-1-retries")).textContent).toBe("1 retry");
    expect(screen.getByTestId("escalated-task-2-retries").textContent).toBe("4 retries");
  });

  it("traduce el estado vacío y el desplegable del historial", async () => {
    wire();
    mount("en");

    const history = await screen.findByTestId("escalated-task-1-history");
    expect(history.textContent).toContain("View history (1 event)");
    expect(history.textContent).not.toContain("Ver historial");

    cleanup();
    wire({ tasks: [] });
    mount("en");
    const empty = await screen.findByTestId("escalated-empty");
    expect(empty.textContent).toBe("No escalated tasks in this plan.");
    expect(empty.textContent).not.toContain("Sin tareas escaladas");
  });

  it("traduce el diálogo de tarea libre por completo", async () => {
    wire();
    mount("en");

    fireEvent.click(await screen.findByTestId("free-task-open"));
    await waitFor(() => expect(screen.getByTestId("free-task-title")).toBeTruthy());

    const dialog = within(screen.getByTestId("free-task-dialog"));
    expect(dialog.getByText("Add a free task to the plan")).toBeDefined();
    expect(dialog.getByText(/not tied to any checkbox of the spec/)).toBeDefined();
    expect(dialog.getByText("Title")).toBeDefined();
    expect(dialog.getByText("Description")).toBeDefined();
    expect(screen.getByTestId("free-task-cancel").textContent).toBe("Cancel");
    expect(screen.getByTestId("free-task-submit").textContent).toBe("Add task");

    expect(dialog.queryByText("Título")).toBeNull();
    expect(dialog.queryByText(/Crea una tarea plan-scoped/)).toBeNull();
  });

  it("no pinta el cuerpo crudo del backend cuando el alta de tarea libre falla", async () => {
    const { ApiError } = await import("@/lib/api");
    wire();
    mount("en");

    fireEvent.click(await screen.findByTestId("free-task-open"));
    await waitFor(() => expect(screen.getByTestId("free-task-title")).toBeTruthy());

    // `mutation.error?.message` era `api {status}: {body}`, o sea el cuerpo
    // crudo con un prefijo: la misma fuga que el hub del proyecto.
    apiFetchMock.mockRejectedValue(new ApiError(500, "<html>nginx traceback</html>"));
    fireEvent.change(screen.getByTestId("free-task-title"), { target: { value: "Revisar" } });
    fireEvent.click(screen.getByTestId("free-task-submit"));

    const error = await screen.findByTestId("free-task-error");
    expect(error.textContent).not.toContain("nginx");
    expect(error.textContent).not.toContain("<html>");
    expect(error.textContent).toBe(
      "The server failed. If it keeps happening, contact an administrator.",
    );
  });

  it("formatea la fecha del historial con el locale del idioma activo", async () => {
    wire();
    mount("en");

    const history = await screen.findByTestId("escalated-task-1-history");
    // `es-ES` escribe `9/10/2025`; `en-GB`, `09/10/2025`. Lo que se afirma es
    // que el locale NO está cableado: el mes va detrás del día en los dos, así
    // que se compara contra el formateo que el propio Intl produce.
    const expected = new Date(1_760_000_000 * 1000).toLocaleString("en-GB");
    expect(history.textContent).toContain(expected);
  });
});
