// @vitest-environment jsdom
// `task_ui_02`: el dashboard del tenant con las seis secciones de `GET /tenant/dashboard`,
// el caso «nada pendiente» y la salud que sólo se enseña cuando duele.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import { ApiError } from "@/lib/api";
import DashboardPage from "@/app/admin/dashboard/page";
import {
  ageParts,
  headlineBudget,
  progressPercent,
  type TenantDashboard,
} from "@/lib/dashboard/tenant-dashboard";
import { LanguageProvider } from "@/lib/lang-context";

configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const FULL: TenantDashboard = {
  pending: {
    total: 4,
    by_kind: { plan_validation: 2, approval_request: 2 },
    oldest: [
      {
        kind: "plan_validation",
        id: "p1",
        title: "Plan esperando",
        project_name: "Web",
        age_seconds: 5 * 3600,
        url_path: "/admin/projects/w/plans/p1",
      },
    ],
  },
  projects: [
    {
      project_id: "w",
      project_name: "Web",
      active_plans: 1,
      tasks_done: 1,
      tasks_total: 4,
      running: 2,
      last_failure: {
        task_title: "Migrar login",
        execution_id: "f1",
        url_path: "/admin/executions/f1",
      },
      url_path: "/admin/projects/w",
    },
  ],
  spend: {
    available: true,
    currency: "USD",
    tenant: {
      scope: "tenant",
      project_id: null,
      project_name: null,
      budget_usd: "100",
      spend_usd: "42.50",
      percent_used: 42.5,
      status: "ok",
      over_budget: false,
      period: "monthly",
    },
    projects: [],
  },
  issues: [
    {
      kind: "mcp_egress_blocked",
      project_id: "w",
      project_name: "Web",
      count: 1,
      url_path: "/admin/projects/w/mcp-servers",
    },
  ],
  activity: [
    {
      execution_id: "e1",
      status: "done",
      task_title: "Escribir la landing",
      project_name: "Web",
      agent_name: "Frontend Dev",
      at: "2026-09-29T10:00:00Z",
      url_path: "/admin/executions/e1",
    },
  ],
  mcp: [{ project_id: "w", project_name: "Web", servers: 2, warnings: 1 }],
  generated_at: "2026-09-29T10:00:00Z",
};

const EMPTY: TenantDashboard = {
  pending: { total: 0, by_kind: {}, oldest: [] },
  projects: [],
  spend: { available: false, reason: "no_budget_configured" },
  issues: [],
  activity: [],
  mcp: [],
  generated_at: "2026-09-29T10:00:00Z",
};

function wire(dashboard: TenantDashboard, health: unknown | Error) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/tenant/dashboard") return Promise.resolve(dashboard);
    if (path === "/admin/system-health") {
      return health instanceof Error ? Promise.reject(health) : Promise.resolve(health);
    }
    return Promise.reject(new Error(`ruta no esperada: ${path}`));
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <DashboardPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("dashboard del tenant (task_ui_02)", () => {
  it("pinta las seis secciones con los datos del endpoint", async () => {
    wire(FULL, { status: "ok", services: [{ name: "postgres", status: "ok" }] });
    mount();
    await screen.findByTestId("dashboard-grid");
    for (const id of [
      "dashboard-pending",
      "dashboard-projects",
      "dashboard-spend",
      "dashboard-issues",
      "dashboard-activity",
      "dashboard-mcp",
    ]) {
      expect(screen.getByTestId(id)).toBeTruthy();
    }
    expect(screen.getByText("Plan esperando")).toBeTruthy();
    expect(screen.getByTestId("dashboard-pending-more")).toBeTruthy();
    expect(screen.getByTestId("dashboard-spend-percent").textContent).toContain("43");
    expect(screen.getByText("Escribir la landing")).toBeTruthy();
    // Un servicio `ok` no se enseña: la salud sólo aparece cuando duele.
    expect(screen.queryByTestId("service-postgres")).toBeNull();
  });

  it("el caso «nada pendiente» no inventa nada y oculta la salud", async () => {
    wire(EMPTY, { status: "ok", services: [] });
    mount();
    await screen.findByTestId("dashboard-grid");
    expect(screen.getByTestId("dashboard-pending-empty")).toBeTruthy();
    expect(screen.getByTestId("dashboard-projects-empty")).toBeTruthy();
    expect(screen.getByTestId("dashboard-spend-none")).toBeTruthy();
    expect(screen.getByTestId("dashboard-activity-empty")).toBeTruthy();
    expect(screen.getByTestId("dashboard-mcp-empty")).toBeTruthy();
    expect(screen.queryByTestId("dashboard-issues")).toBeNull();
  });

  it("un servicio degradado sí aparece, en la sección de atención", async () => {
    wire(EMPTY, { status: "degraded", services: [{ name: "vault", status: "down" }] });
    mount();
    await waitFor(() => expect(screen.getByTestId("service-vault")).toBeTruthy());
    expect(screen.getByTestId("dashboard-issues")).toBeTruthy();
  });

  it("sin permiso para leer la salud del stack, el dashboard sale igual", async () => {
    wire(EMPTY, new Error("403"));
    mount();
    await screen.findByTestId("dashboard-grid");
    expect(screen.queryByTestId("dashboard-error")).toBeNull();
  });
});

describe("gasto, último fallo y tenant activo (revisión task_ui_02)", () => {
  it("la tarjeta de gasto enlaza a /admin/tenant-stats", async () => {
    wire(FULL, { status: "ok", services: [] });
    mount();
    const link = await screen.findByTestId("dashboard-spend-link");
    expect(link.getAttribute("href")).toBe("/admin/tenant-stats");
  });

  it("el último fallo del proyecto se pinta con enlace al run", async () => {
    wire(FULL, { status: "ok", services: [] });
    mount();
    const link = await screen.findByTestId("dashboard-project-w-failure");
    expect(link.textContent).toContain("Migrar login");
    expect(link.getAttribute("href")).toBe("/admin/executions/f1");
  });

  it("gasto «forbidden» oculta la tarjeta: no dice que no hay presupuesto", async () => {
    wire(
      { ...EMPTY, spend: { available: false, reason: "forbidden" } },
      { status: "ok", services: [] },
    );
    mount();
    await screen.findByTestId("dashboard-grid");
    expect(screen.queryByTestId("dashboard-spend")).toBeNull();
    expect(screen.queryByTestId("dashboard-spend-none")).toBeNull();
  });

  it("un System Admin sin tenant activo ve el aviso de elegir tenant, no el error", async () => {
    apiFetchMock.mockImplementation((path: string) =>
      path === "/tenant/dashboard"
        ? Promise.reject(new ApiError(403, '{"detail":"no active tenant context"}'))
        : Promise.reject(new Error("403")),
    );
    mount();
    const notice = await screen.findByTestId("dashboard-no-tenant");
    expect(notice.querySelector("a")?.getAttribute("href")).toBe("/select-tenant");
    expect(screen.queryByTestId("dashboard-error")).toBeNull();
  });
});

describe("helpers puros del dashboard", () => {
  it("progressPercent redondea y no divide por cero", () => {
    expect(progressPercent(1, 3)).toBe(33);
    expect(progressPercent(0, 0)).toBe(0);
  });

  it("ageParts usa la unidad más grande que cabe", () => {
    expect(ageParts(300)).toEqual({ value: 5, unit: "m" });
    expect(ageParts(5 * 3600)).toEqual({ value: 5, unit: "h" });
    expect(ageParts(3 * 86400)).toEqual({ value: 3, unit: "d" });
  });

  it("headlineBudget prefiere el del tenant y si no el proyecto más consumido", () => {
    expect(headlineBudget(EMPTY.spend)).toBeNull();
    expect(headlineBudget(FULL.spend)?.scope).toBe("tenant");
    const projectOnly = {
      available: true,
      tenant: null,
      projects: [
        { ...FULL.spend.tenant!, scope: "project", project_name: "A", percent_used: 10 },
        { ...FULL.spend.tenant!, scope: "project", project_name: "B", percent_used: 80 },
      ],
    };
    expect(headlineBudget(projectOnly)?.project_name).toBe("B");
  });
});
