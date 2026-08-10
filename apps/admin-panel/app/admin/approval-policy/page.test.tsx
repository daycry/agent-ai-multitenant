// @vitest-environment jsdom
// ADR 0153 (C) — la clave `unlisted_category` tiene que VERSE y poder EDITARSE.
//
// Sin esta pantalla la clave sigue existiendo y decidiendo, pero solo se puede
// leer abriendo el JSONB de la fila: una política que falla cerrado sin que se
// vea dónde se configura eso genera un ticket de soporte por proyecto.
//
// Y hay un segundo motivo, menos obvio: la pantalla COPIA el preset al
// proyecto. Si al guardar no arrastrase la clave, cada «Aplicar política»
// borraría lo que la migración de datos acababa de escribir.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import ApprovalPolicyPage from "@/app/admin/approval-policy/page";

const CATEGORIES = [
  "code_changes",
  "git_commit",
  "git_push",
  "external_http_get",
  "external_http_post",
  "secrets_access",
  "data_migration",
  "production_deploy",
  "infra_provision",
  "secret_rotation",
  "external_communication",
  "data_export_pii",
  "user_management",
];

function decisions(value: string): Record<string, string> {
  return Object.fromEntries(CATEGORIES.map((c) => [c, value]));
}

const SANDBOX = {
  id: "pol-sandbox",
  name: "Sandbox",
  description: "Todo auto",
  is_builtin: true,
  categories: { preset: "sandbox", categories: decisions("auto"), unlisted_category: "auto" },
};

const PRODUCTION = {
  id: "pol-production",
  name: "Produccion",
  description: "Estricto",
  is_builtin: true,
  categories: {
    preset: "production",
    categories: decisions("human_required"),
    unlisted_category: "human_required",
  },
};

const PROJECTS = [
  { id: "proj-1", name: "Mi API", is_template: false, human_approval_policy: null },
];

function mockApi(policies: unknown[] = [SANDBOX, PRODUCTION]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (String(path).startsWith("/approval-policies")) return Promise.resolve(policies);
    if (String(path) === "/projects") return Promise.resolve(PROJECTS);
    return Promise.resolve({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ApprovalPolicyPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ApprovalPolicyPage — categoria no listada (ADR 0153)", () => {
  it("pinta la clave del preset activo", async () => {
    mockApi();
    renderPage();

    const row = await screen.findByTestId("unlisted-category-row");
    expect(row.getAttribute("data-decision")).toBe("auto"); // Sandbox se autoselecciona
  });

  it("cambia con el preset: Produccion la trae en humano", async () => {
    mockApi();
    renderPage();

    fireEvent.click(await screen.findByTestId("preset-pol-production"));

    await waitFor(() =>
      expect(screen.getByTestId("unlisted-category-row").getAttribute("data-decision")).toBe(
        "human_required",
      ),
    );
  });

  it("se puede editar, y el cambio cuenta como pendiente de guardar", async () => {
    mockApi();
    renderPage();

    const row = await screen.findByTestId("unlisted-category-row");
    expect(screen.queryByTestId("dirty-badge")).toBeNull();

    fireEvent.click(screen.getByTestId("toggle-unlisted-category"));

    expect(row.getAttribute("data-decision")).toBe("human_required");
    expect(row.getAttribute("data-override")).toBe("true");
    expect(screen.getByTestId("dirty-badge")).toBeTruthy();
  });

  it("guarda la clave en la politica del proyecto, junto al preset", async () => {
    mockApi();
    renderPage();

    fireEvent.click(await screen.findByTestId("preset-pol-production"));
    fireEvent.change(screen.getByTestId("project-select"), { target: { value: "proj-1" } });
    fireEvent.click(screen.getByTestId("save-policy"));

    await waitFor(() =>
      expect(apiFetchMock.mock.calls.some(([path]) => String(path) === "/projects/proj-1")).toBe(
        true,
      ),
    );
    const put = apiFetchMock.mock.calls.find(([path]) => String(path) === "/projects/proj-1");
    const policy = (put?.[1] as { body: { human_approval_policy: Record<string, unknown> } }).body
      .human_approval_policy;

    expect(policy.unlisted_category).toBe("human_required");
    expect(policy.preset).toBe("production");
    expect(Object.keys(policy.categories as Record<string, string>)).toHaveLength(13);
  });

  it("una politica sembrada SIN la clave se pinta fail-closed, no auto", async () => {
    // El caso que se ve en un despliegue a medio migrar: el preset viejo en la
    // tabla y el motor nuevo leyendo. Pintar «Auto» ahí mentiria sobre lo que
    // el gate va a hacer.
    mockApi([
      {
        ...PRODUCTION,
        categories: { categories: decisions("human_required") }, // sin preset ni clave
      },
    ]);
    renderPage();

    const row = await screen.findByTestId("unlisted-category-row");
    expect(row.getAttribute("data-decision")).toBe("human_required");
  });
});
