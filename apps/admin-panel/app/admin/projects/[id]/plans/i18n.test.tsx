// @vitest-environment jsdom

/**
 * `projects/[id]/plans` y `plans/[planId]/*` COMPLETOS, migrados al diccionario
 * (plan prod-16, `task_prod16_03`).
 *
 * Por qué entra el módulo entero y no lo que marcaba la guarda: la
 * `ATTR_ALLOWLIST` le veía **6 atributos en 5 ficheros** (`plans/page.tsx` 4,
 * `[planId]/page.tsx` 1, `plan-spec-sections` 2, `plan-validation-section` 1,
 * más uno en cada diagrama de `lib/`) de un módulo de ~2.900 líneas repartidas
 * en dieciséis. Todo lo demás es texto JSX suelto y frases de ayuda, que es
 * donde ninguna de las dos señales mira, y **dos módulos puros** —
 * `plan-spec-types.ts` con el catálogo de estados y `lib/plan-spec-edit.ts` con
 * los mensajes de validación del editor— donde no mira ninguna de las dos.
 *
 * Y una duplicación que la traducción obligó a resolver: `STATUS_LABEL` existía
 * **dos veces**, copiado byte a byte en el listado y en el detalle. Traducir dos
 * copias del mismo enum del backend es garantizar que divergen, así que ahora
 * hay una sola, guarda la CLAVE, y el listado la importa.
 *
 * Se afirma la pantalla en los dos idiomas incluyendo **los dos diálogos** (el
 * de rechazo y el de sincronización al Kanban) y **el editor del spec**, que es
 * donde vive más de la mitad del texto y donde un `useT()` olvidado no se ve
 * hasta que alguien pulsa el botón.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1", planId: "plan-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/plans",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// El lanzador de preview es de otro carril y hace su propio fetch.
vi.mock("@/components/projects/preview-launcher", () => ({ PreviewLauncher: () => null }));

import ProjectPlansPage from "@/app/admin/projects/[id]/plans/page";
import PlanDetailPage from "@/app/admin/projects/[id]/plans/[planId]/page";

const STORAGE_KEY = "admin-panel.lang";

const PLAN_LIST_ITEM = {
  id: "plan-1",
  tenant_id: "t1",
  project_id: "proj-1",
  title: "Migrar el esquema",
  description: null,
  status: "draft",
  conversation_id: null,
  specification: {},
  created_at: "2026-08-01T00:00:00Z",
};

const SPEC = {
  summary: {
    description: "Un resumen",
    scope_in: ["dentro"],
    scope_out: ["fuera"],
    decisions: ["una decisión"],
    risks: [{ name: "riesgo", mitigation: "mitigación" }],
  },
  phases: [{ title: "", description: "desc", tasks: ["t1"] }],
  tasks: [
    { id: "t1", title: "Primera", role: "backend_dev", complexity: "m", depends_on: [] },
    { id: "t2", title: "Segunda", origin: "correction", depends_on: ["t1"] },
  ],
  estimates: { duration_calendar: "2 semanas", effort_person_days: 4 },
};

const PLAN_STATUS = {
  plan_id: "plan-1",
  status: "draft",
  progress: { total: 2, done: 1, open: 1, label: "1/2" },
  pr: { url: null, branch: null, error: null },
  cost: {
    ai_currency: "USD",
    human_currency: "EUR",
    estimated_ai_min: "1",
    estimated_ai_max: "2",
    estimated_human_hours: "8",
    estimated_human_cost: "400",
    actual_ai_cost: "0.5",
    actual_tokens: 1200,
    actual_runs: 1,
    over_estimate: false,
  },
};

const PREFLIGHT = {
  task_count: 2,
  blockers: 0,
  warnings: 0,
  critical_path: ["t1", "t2"],
  critical_path_length: 2,
  max_parallelism: 1,
  findings: [],
};

function plan(status: string, extra: Record<string, unknown> = {}) {
  return {
    id: "plan-1",
    title: "Migrar el esquema",
    description: null,
    status,
    conversation_id: null,
    specification: SPEC,
    approved_by: null,
    approved_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...extra,
  };
}

function wireList(plans: unknown[] = [PLAN_LIST_ITEM]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/projects/proj-1/plans") return Promise.resolve(plans);
    return Promise.resolve([]);
  });
}

const COST_BREAKDOWN = {
  human: {
    currency: "EUR",
    hourly_rate: "50",
    total_hours: "8",
    total_cost: "400",
    tasks: [{ task_id: "t1", title: "Primera", hours: "8", cost: "400" }],
  },
  ai: {
    currency: "USD",
    default_model_id: "sonnet",
    cost_min: "1",
    cost_max: "2",
    tasks: [
      {
        task_id: "t1",
        title: "Primera",
        complexity: "m",
        model_id: "sonnet",
        tokens_in_min: 1,
        tokens_in_max: 2,
        tokens_out_min: 1,
        tokens_out_max: 2,
        cost_min: "1",
        cost_max: "2",
      },
    ],
    missing_models: [],
  },
};

function wireDetail(status = "draft", extra: Record<string, unknown> = {}) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/plans/plan-1") return Promise.resolve(plan(status, extra));
    if (path === "/plans/plan-1/status") return Promise.resolve(PLAN_STATUS);
    if (path === "/plans/plan-1/preflight") return Promise.resolve(PREFLIGHT);
    if (path === "/plans/plan-1/cost-breakdown") return Promise.resolve(COST_BREAKDOWN);
    if (path === "/plans/plan-1/comments") return Promise.resolve([]);
    if (path === "/plans/plan-1/review-session") {
      return Promise.resolve({
        session_id: "s1",
        status: "running",
        verdict: null,
        rejection_reason: "Falta el filtro",
        expires_at: "2026-08-21T10:00:00Z",
        review_url: "https://x/review",
        app_url: "https://x/app",
        verdict_url: "https://x/verdict",
      });
    }
    return Promise.resolve([]);
  });
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

const list = (lang: "es" | "en", plans?: unknown[]) => {
  wireList(plans);
  return renderIn(lang, <ProjectPlansPage />);
};

const detail = (lang: "es" | "en", status = "draft", extra: Record<string, unknown> = {}) => {
  wireDetail(status, extra);
  return renderIn(lang, <PlanDetailPage />);
};

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("listado de planes en los dos idiomas", () => {
  it("rinde cabecera, filtros y fila en castellano", async () => {
    list("es");

    expect(await screen.findByText("Planes del proyecto")).toBeDefined();
    expect(screen.getByTestId("plans-filter-all").textContent).toContain("Todos");
    expect(screen.getByTestId("plans-filter-draft").textContent).toContain("Borrador");
    await waitFor(() => expect(screen.getByTestId("plans-list")).toBeTruthy());
    expect(screen.getByTestId("plan-row-plan-1-badge").textContent).toBe("Borrador");
    expect(screen.getByText("Sin descripción")).toBeDefined();
  });

  it("traduce cabecera, filtros, badges y el estado vacío", async () => {
    list("en", []);

    expect(await screen.findByText("Project plans")).toBeDefined();
    expect(screen.getByText(/Each plan groups phases, tasks and dependencies/)).toBeDefined();
    expect(screen.getByRole("button", { name: /Generate from chat/ })).toBeDefined();
    expect(screen.getByTestId("plans-status-filter").getAttribute("aria-label")).toBe(
      "Filter plans by status",
    );
    expect(screen.getByTestId("plans-filter-all").textContent).toContain("All");
    expect(screen.getByTestId("plans-filter-pending_approval").textContent).toContain(
      "Pending approval",
    );
    const empty = await screen.findByTestId("plans-empty");
    expect(empty.textContent).toContain("This project has no plans yet");

    expect(screen.queryByText("Planes del proyecto")).toBeNull();
    expect(screen.queryByText(/Este proyecto aún no tiene planes/)).toBeNull();
  });

  it("traduce el estado vacío del filtro y la fila sin descripción", async () => {
    list("en");

    await waitFor(() => expect(screen.getByTestId("plans-list")).toBeTruthy());
    expect(screen.getByTestId("plan-row-plan-1-badge").textContent).toBe("Draft");
    expect(screen.getByText("No description")).toBeDefined();

    fireEvent.click(screen.getByTestId("plans-filter-completed"));
    const empty = await screen.findByTestId("plans-empty");
    expect(empty.textContent).toBe("No plans in this status.");
  });
});

describe("detalle del plan en los dos idiomas", () => {
  it("rinde secciones y el ciclo de vida en castellano", async () => {
    detail("es");

    expect(await screen.findByTestId("plan-detail")).toBeDefined();
    expect(screen.getByTestId("plan-detail-status-badge").textContent).toBe("Borrador");
    await waitFor(() => expect(screen.getByTestId("plan-lifecycle")).toBeTruthy());
    expect(screen.getByTestId("plan-send-to-approval").textContent).toContain(
      "Enviar a aprobación",
    );
    expect(screen.getByText("Resumen")).toBeDefined();
    expect(screen.getByText("Fases")).toBeDefined();
  });

  it("traduce cabecera de estado, preflight y ciclo de vida", async () => {
    detail("en");

    await waitFor(() => expect(screen.getByTestId("plan-status-header")).toBeTruthy());
    expect(screen.getByTestId("plan-status-progress").textContent).toContain("Progress");
    expect(screen.getByTestId("plan-status-pr-none").textContent).toBe("No PR yet");
    expect(screen.getByTestId("plan-status-cost").textContent).toContain("Actual / estimated cost");

    await waitFor(() => expect(screen.getByTestId("plan-preflight")).toBeTruthy());
    expect(screen.getByText("Before approving")).toBeDefined();
    expect(screen.getByTestId("preflight-clean").textContent).toContain(
      "All 2 tasks have an assignable role",
    );

    expect(screen.getByText("Plan lifecycle")).toBeDefined();
    expect(screen.getByTestId("plan-send-to-approval").textContent).toContain("Send for approval");
    expect(screen.queryByText("Ciclo de vida del plan")).toBeNull();
  });

  it("traduce las secciones presentacionales y la tabla de tareas", async () => {
    detail("en");

    expect(await screen.findByText("Summary")).toBeDefined();
    expect(screen.getByTestId("plan-scope-in").textContent).toContain("In scope");
    expect(screen.getByTestId("plan-scope-out").textContent).toContain("Out of scope");
    expect(screen.getByTestId("plan-decisions").textContent).toContain("Decisions");
    expect(screen.getByTestId("plan-risks").textContent).toContain("Risks");
    expect(screen.getByText("Estimates")).toBeDefined();
    expect(screen.getByTestId("estimate-effort").textContent).toContain("Effort (person-days)");
    expect(screen.getByText("Phases")).toBeDefined();
    // La fase del fixture no trae `title` ni `name`: cae al rótulo de respaldo.
    expect(screen.getByTestId("plan-phase-0").textContent).toContain("Phase 1");
    expect(screen.getByText("Tasks (2)")).toBeDefined();
    expect(screen.getByText("Depends on")).toBeDefined();
    expect(screen.getByTestId("plan-task-origin-t2").textContent).toBe("fix");
    expect(screen.getByText("Dependency graph")).toBeDefined();

    expect(screen.queryByText("Grafo de dependencias")).toBeNull();
    expect(screen.queryByText("Fuera de alcance")).toBeNull();
  });

  it("traduce los comentarios, incluido el selector de destino", async () => {
    detail("en");

    await waitFor(() => expect(screen.getByTestId("plan-comments")).toBeTruthy());
    expect(screen.getByText("Comments")).toBeDefined();
    expect(screen.getByTestId("plan-comments-empty").textContent).toBe("No comments yet.");
    const kind = screen.getByTestId("plan-comment-target-kind");
    expect(within(kind).getByText("On the plan")).toBeDefined();
    expect(within(kind).getByText("On a task")).toBeDefined();
    // `MarkdownTextarea` cuelga su textarea de `<testid>-edit`.
    expect(screen.getByTestId("plan-comment-content-edit").getAttribute("placeholder")).toBe(
      "Write your comment…",
    );
    expect(screen.getByTestId("plan-comment-submit").textContent).toBe("Comment");
  });

  it("traduce el editor del spec y sus mensajes de validación", async () => {
    detail("en");

    fireEvent.click(await screen.findByTestId("plan-spec-edit-open"));
    await waitFor(() => expect(screen.getByTestId("plan-spec-editor")).toBeTruthy());

    expect(screen.getByText("Edit tasks (2)")).toBeDefined();
    // Las etiquetas se repiten una vez por tarea: dos filas, dos «Description».
    expect(screen.getAllByText("Description")).toHaveLength(2);
    expect(screen.getAllByText("Estimated hours")).toHaveLength(2);
    expect(screen.getAllByText("Acceptance criteria (one per line)")).toHaveLength(2);
    expect(screen.getByTestId("plan-spec-remove-0").getAttribute("aria-label")).toBe(
      "Remove task t1",
    );
    expect(screen.getByTestId("plan-spec-complexity-0").getAttribute("placeholder")).toBe("medium");
    expect(screen.getByTestId("plan-spec-save").textContent).toBe("Save changes");

    // Y el mensaje de validación, que vive en un módulo PURO (`lib/plan-spec-edit`)
    // donde ninguna de las dos guardas mira.
    fireEvent.change(screen.getByTestId("plan-spec-title-0"), { target: { value: "" } });
    await waitFor(() => expect(screen.getByTestId("plan-spec-problems")).toBeTruthy());
    expect(screen.getByTestId("plan-spec-problems").textContent).toContain(
      "Task “t1” has no title",
    );
  });

  it("traduce el diálogo de sincronización al Kanban", async () => {
    detail("en", "approved");

    await waitFor(() => expect(screen.getByTestId("plan-sync-open")).toBeTruthy());
    // El mismo rótulo en la tarjeta y en el botón: por eso `getAllByText`.
    expect(screen.getAllByText("Sync to the Kanban").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTestId("plan-sync-open").textContent).toBe("Sync to the Kanban");
    fireEvent.click(screen.getByTestId("plan-sync-open"));

    await waitFor(() => expect(screen.getByTestId("plan-sync-dialog")).toBeTruthy());
    const dialog = within(screen.getByTestId("plan-sync-dialog"));
    expect(dialog.getByText("Whole plan (2 tasks)")).toBeDefined();
    expect(dialog.getByText("One phase")).toBeDefined();
    expect(dialog.getByText("Custom selection")).toBeDefined();
    expect(screen.getByTestId("plan-sync-confirm").textContent).toBe("Sync");
    expect(screen.getByTestId("plan-sync-cancel").textContent).toBe("Cancel");
  });

  it("traduce la validación humana y su diálogo de rechazo", async () => {
    detail("en", "pending_human_validation");

    // La sesión de review llega en una segunda consulta: los botones no existen
    // hasta que responde.
    await waitFor(() => expect(screen.getByTestId("plan-open-app")).toBeTruthy());
    const card = screen.getByTestId("plan-human-validation");
    expect(card.textContent).toContain("Human validation — try the app");
    expect(card.textContent).toContain("brought up in a review container");
    expect(screen.getByTestId("plan-open-app").textContent).toContain("Open the app to try it");
    expect(screen.getByTestId("plan-verdict-reject").textContent).toContain("Reject");

    fireEvent.click(screen.getByTestId("plan-verdict-reject"));
    await waitFor(() => expect(screen.getByTestId("plan-reject-dialog")).toBeTruthy());
    const dialog = within(screen.getByTestId("plan-reject-dialog"));
    // El rótulo sale en el título del diálogo Y en su botón de confirmar.
    expect(dialog.getAllByText("Reject plan")).toHaveLength(2);
    expect(dialog.getByText(/The reason reaches the agents as rework feedback/)).toBeDefined();
    expect(screen.getByTestId("plan-reject-reason-edit").getAttribute("placeholder")).toContain(
      "Content-Type filter is global",
    );

    expect(card.textContent).not.toContain("Validación humana");
  });

  it("traduce los deep links y las correcciones del rechazo", async () => {
    detail("en", "rejected");

    await waitFor(() => expect(screen.getByTestId("plan-deep-links")).toBeTruthy());
    expect(screen.getByText("Plan panels")).toBeDefined();
    expect(screen.getByTestId("plan-link-escalated").textContent).toContain(
      "Escalated and blocked tasks",
    );

    await waitFor(() => expect(screen.getByTestId("plan-corrections")).toBeTruthy());
    const corrections = screen.getByTestId("plan-corrections");
    expect(corrections.textContent).toContain("Rejection fixes");
    expect(screen.getByTestId("plan-corrections-reason").textContent).toContain("Validator reason");
    expect(screen.getByTestId("plan-corrections-generate").textContent).toBe(
      "Generate corrective tasks",
    );

    expect(corrections.textContent).not.toContain("Correcciones del rechazo");
  });

  it("traduce el diff de código de la rama al desplegarlo", async () => {
    detail("en");

    fireEvent.click(await screen.findByTestId("plan-code-diff-toggle"));
    expect(screen.getByText("Branch code diff")).toBeDefined();
  });

  /**
   * El desglose de coste es el aviso más incómodo del lote: **ya usaba
   * `useT("planCost")` y no tenía ni un atributo con castellano**, así que las
   * dos guardas lo daban por migrado… y seguía pintando el título de la
   * tarjeta, el texto de carga y las dos cabeceras de tabla en castellano fijo.
   * Un fichero a medio migrar es indistinguible de uno migrado para un guard que
   * mira patrones.
   */
  it("traduce el desglose de coste, que estaba a medio migrar", async () => {
    detail("en");

    await waitFor(() => expect(screen.getByTestId("plan-cost-breakdown")).toBeTruthy());
    const card = screen.getByTestId("plan-cost-breakdown");
    expect(card.textContent).toContain("Cost breakdown");
    expect(card.textContent).toContain("Human cost · EUR");
    expect(card.textContent).toContain("AI cost · USD · default model");
    expect(card.textContent).toContain("Min cost");
    expect(card.textContent).toContain("Total (range)");

    expect(card.textContent).not.toContain("Desglose de coste");
    expect(card.textContent).not.toContain("Coste mín");
  });
});
