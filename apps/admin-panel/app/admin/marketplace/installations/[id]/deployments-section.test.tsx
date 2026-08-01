// @vitest-environment jsdom
//
// «Desplegado en N proyectos» + «Desplegar a…» + retirar (ADR 0142,
// `task_mkt2_06`). Es la puerta 2 de las tres, y la única desde la que se ve el
// mapa completo: qué proyectos tienen esta instalación y con qué versión.
//
// Lo que se clava aquí, y por qué cada cosa:
//
//   * la lista enseña TAMBIÉN los `retired` (la ficha es historial, no sólo lo
//     vivo) y los distingue;
//   * el multi-select ofrece un formulario POR PROYECTO — el caso que el modelo
//     viejo no sabía expresar era justo dos proyectos con `base_url` distinta;
//   * el POST manda `role_map` como lista y `config` con los defaults aplicados;
//   * un `already_deployed` se cuenta como tal y NO como un despliegue nuevo, y
//     los `warnings` / `oauth_pending` se enseñan: un despliegue que no entregó
//     lo que prometía tiene que decirlo.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { DeploymentsSection } from "@/app/admin/marketplace/installations/[id]/deployments-section";
import type { CapabilityShape } from "@/components/marketplace/deployment-types";

const INSTALLATION_ID = "inst-1";

const CAPABILITY: CapabilityShape = {
  config_schema: {
    properties: {
      base_url: { type: "string", title: "Base URL", default: null },
      timeout_ms: { type: "integer", title: "Timeout", default: 30000, minimum: 1 },
    },
    required: [],
  },
  targets: ["backend_dev"],
};

const PROJECTS = [
  { id: "proj-a", name: "App A" },
  { id: "proj-b", name: "App B" },
];

function deployment(over: Record<string, unknown> = {}) {
  return {
    id: "dep-1",
    tenant_id: "t1",
    installation_id: INSTALLATION_ID,
    project_id: "proj-a",
    config: { base_url: "https://a.example" },
    role_map: { "*": ["backend_dev"] },
    deployed_version: "1.2.0",
    status: "active",
    created_refs: { mcp_servers: ["jira"] },
    deployed_by: null,
    retired_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...over,
  };
}

function wireApi({
  deployments = [] as Record<string, unknown>[],
  deployResponse = null as Record<string, unknown> | null,
  deployError = null as Error | null,
} = {}) {
  const created = deployResponse ?? {
    deployment: deployment({ id: "dep-new", project_id: "proj-b" }),
    already_deployed: false,
    warnings: [],
    oauth_pending: false,
  };
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    // Desplegar y retirar van envueltos en <RoleGuard min="tenant_admin">, así
    // que sin un `/me` creíble la sección se renderiza sin sus botones y el
    // test fallaría por la razón equivocada.
    if (path === "/me") {
      return Promise.resolve({
        user_id: "u1",
        email: null,
        full_name: null,
        is_system_admin: true,
        memberships: [],
        active_tenant_id: null,
      });
    }
    if (path === `/marketplace/installations/${INSTALLATION_ID}/deployments`) {
      if (opts?.method === "POST") {
        if (deployError) return Promise.reject(deployError);
        return Promise.resolve(created);
      }
      return Promise.resolve(deployments);
    }
    if (path.startsWith("/projects")) return Promise.resolve(PROJECTS);
    if (path.includes("/retire")) return Promise.resolve({ removed_refs: 2 });
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeploymentsSection installationId={INSTALLATION_ID} capability={CAPABILITY} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("DeploymentsSection", () => {
  it("dice que no hay despliegues cuando no los hay (instalar no es desplegar)", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("deployments-empty")).toBeTruthy());
    expect(screen.queryByTestId("deployments-list")).toBeNull();
  });

  it("lista los despliegues con su proyecto, versión y estado, incluidos los retirados", async () => {
    wireApi({
      deployments: [
        deployment(),
        deployment({ id: "dep-2", project_id: "proj-b", status: "retired", retired_at: "x" }),
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("deployment-dep-1")).toBeTruthy());
    expect(screen.getByTestId("deployment-dep-1").textContent).toContain("App A");
    expect(screen.getByTestId("deployment-dep-1").textContent).toContain("1.2.0");
    expect(screen.getByTestId("deployment-dep-2").textContent).toContain("App B");
    // El contador cuenta lo VIVO, no el historial.
    expect(screen.getByTestId("deployments-count").textContent).toContain("1");
    // Retirar sólo se ofrece sobre lo activo.
    expect(screen.getByTestId("deployment-retire-dep-1")).toBeTruthy();
    expect(screen.queryByTestId("deployment-retire-dep-2")).toBeNull();
  });

  it("despliega en un proyecto con la config del formulario y el role_map como lista", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("deployments-deploy-open")).toBeTruthy());

    fireEvent.click(screen.getByTestId("deployments-deploy-open"));
    fireEvent.click(screen.getByTestId("deployments-project-proj-b"));

    // Un formulario POR PROYECTO: es lo que hace expresables dos `base_url`.
    await waitFor(() => expect(screen.getByTestId("deploy-proj-b-form")).toBeTruthy());
    expect((screen.getByTestId("deploy-proj-b-field-timeout_ms") as HTMLInputElement).value).toBe(
      "30000",
    );
    expect((screen.getByTestId("deploy-proj-b-role-backend_dev") as HTMLInputElement).checked).toBe(
      true,
    );

    fireEvent.change(screen.getByTestId("deploy-proj-b-field-base_url"), {
      target: { value: "https://b.example" },
    });
    fireEvent.click(screen.getByTestId("deployments-deploy-submit"));

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          p === `/marketplace/installations/${INSTALLATION_ID}/deployments` &&
          (o as { method?: string })?.method === "POST",
      );
      expect(post).toBeTruthy();
      expect(post?.[1]?.body).toEqual({
        project_id: "proj-b",
        config: { base_url: "https://b.example", timeout_ms: 30000 },
        role_map: ["backend_dev"],
      });
    });
    await waitFor(() => expect(screen.getByTestId("deploy-result-proj-b")).toBeTruthy());
  });

  it("no ofrece re-desplegar donde ya está activo (la idempotencia se ve antes de pulsar)", async () => {
    wireApi({ deployments: [deployment()] });
    mount();
    await waitFor(() => expect(screen.getByTestId("deployments-deploy-open")).toBeTruthy());
    fireEvent.click(screen.getByTestId("deployments-deploy-open"));
    expect((screen.getByTestId("deployments-project-proj-a") as HTMLInputElement).disabled).toBe(
      true,
    );
    expect((screen.getByTestId("deployments-project-proj-b") as HTMLInputElement).disabled).toBe(
      false,
    );
  });

  it("enseña los avisos y el OAuth pendiente en vez de fingir que quedó vivo", async () => {
    wireApi({
      deployResponse: {
        deployment: deployment({ id: "dep-new", project_id: "proj-b" }),
        already_deployed: true,
        warnings: ["ningún agente del proyecto tiene los roles ['backend_dev']"],
        oauth_pending: true,
      },
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("deployments-deploy-open")).toBeTruthy());
    fireEvent.click(screen.getByTestId("deployments-deploy-open"));
    fireEvent.click(screen.getByTestId("deployments-project-proj-b"));
    fireEvent.click(screen.getByTestId("deployments-deploy-submit"));

    await waitFor(() => expect(screen.getByTestId("deploy-warnings-proj-b")).toBeTruthy());
    expect(screen.getByTestId("deploy-warnings-proj-b").textContent).toContain("ningún agente");
    expect(screen.getByTestId("deploy-oauth-proj-b")).toBeTruthy();
    // `already_deployed` NO se cuenta como un despliegue nuevo.
    expect(screen.getByTestId("deploy-result-proj-b").getAttribute("data-outcome")).toBe("already");
  });

  it("un despliegue que falla no se traga: se reporta por-item", async () => {
    wireApi({ deployError: new Error("boom") });
    mount();
    await waitFor(() => expect(screen.getByTestId("deployments-deploy-open")).toBeTruthy());
    fireEvent.click(screen.getByTestId("deployments-deploy-open"));
    fireEvent.click(screen.getByTestId("deployments-project-proj-b"));
    fireEvent.click(screen.getByTestId("deployments-deploy-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("deploy-result-proj-b").getAttribute("data-outcome")).toBe(
        "failed",
      ),
    );
  });

  it("retirar pide confirmación y llama al endpoint de retirada", async () => {
    wireApi({ deployments: [deployment()] });
    mount();
    await waitFor(() => expect(screen.getByTestId("deployment-retire-dep-1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("deployment-retire-dep-1"));
    // Sin confirmar no se llama a nada.
    expect(apiFetchMock.mock.calls.some(([p]) => String(p).includes("/retire"))).toBe(false);

    fireEvent.click(screen.getByTestId("confirm-dialog-accept"));
    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.some(
          ([p, o]) =>
            p === "/marketplace/deployments/dep-1/retire" &&
            (o as { method?: string })?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("bloquea el submit mientras la config de algún proyecto no valide", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("deployments-deploy-open")).toBeTruthy());
    fireEvent.click(screen.getByTestId("deployments-deploy-open"));
    fireEvent.click(screen.getByTestId("deployments-project-proj-b"));
    await waitFor(() => expect(screen.getByTestId("deploy-proj-b-form")).toBeTruthy());

    expect((screen.getByTestId("deployments-deploy-submit") as HTMLButtonElement).disabled).toBe(
      false,
    );
    fireEvent.change(screen.getByTestId("deploy-proj-b-field-timeout_ms"), {
      target: { value: "0" },
    });
    expect(screen.getByTestId("deploy-proj-b-errors")).toBeTruthy();
    expect((screen.getByTestId("deployments-deploy-submit") as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("sin ningún proyecto marcado no se puede desplegar", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("deployments-deploy-open")).toBeTruthy());
    fireEvent.click(screen.getByTestId("deployments-deploy-open"));
    expect((screen.getByTestId("deployments-deploy-submit") as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
