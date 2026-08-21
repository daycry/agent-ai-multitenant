// @vitest-environment jsdom
//
// Paso «Capacidades» del wizard de proyecto (ADR 0142, `task_mkt2_07`).
//
// Es la puerta 1 de las tres y la que cierra la decisión D3: al crear un
// proyecto se ofrece lo que el tenant ya tiene instalado, y lo marcado queda
// configurado y asignado desde el día 1.
//
// Lo que se clava:
//
//   * el paso ofrece SÓLO lo habilitado (una instalación revocada o pendiente de
//     consentimiento no se puede desplegar: el backend devolvería 409);
//   * marcar una capacidad abre SU formulario, con los defaults del manifest y
//     los roles de `targets` pre-marcados;
//   * un tenant sin nada instalado ve una explicación, no una lista vacía;
//   * el paso no puede continuar mientras alguna config marcada no valide.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  CapabilitiesStep,
  useTenantCapabilities,
} from "@/app/admin/projects/new/capabilities-step";
import type { DeploymentDraft } from "@/components/marketplace/deployment-types";

const INSTALLATIONS = [
  { id: "i1", listing_id: "l1", version: "1.2.0", status: "enabled" },
  { id: "i2", listing_id: "l2", version: "0.9.0", status: "disabled" },
];

const LISTINGS = [
  {
    id: "l1",
    kind: "mcp_server",
    name: "Jira MCP",
    version: "1.2.0",
    description: "Issues y sprints",
    trust_level: "verified",
    manifest: {
      targets: ["backend_dev"],
      config_schema: {
        properties: {
          base_url: { type: "string", title: "Base URL", default: null },
          timeout_ms: { type: "integer", title: "Timeout", default: 30000, minimum: 1 },
        },
        required: [],
      },
    },
  },
  {
    id: "l2",
    kind: "tool",
    name: "No habilitada",
    version: "0.9.0",
    description: null,
    trust_level: "community",
    manifest: {},
  },
];

function wireApi({ installations = INSTALLATIONS, listings = LISTINGS } = {}) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/marketplace/installations")) return Promise.resolve(installations);
    if (path.startsWith("/marketplace/listings")) return Promise.resolve(listings);
    return Promise.resolve([]);
  });
}

/** Arnés: el wizard es quien lleva los borradores, igual que en la página real. */
function Harness() {
  const capabilities = useTenantCapabilities();
  const [drafts, setDrafts] = useState<Record<string, DeploymentDraft>>({});
  return (
    <CapabilitiesStep capabilities={capabilities} drafts={drafts} onDraftsChange={setDrafts} />
  );
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("CapabilitiesStep", () => {
  it("ofrece sólo lo instalado y HABILITADO, con su nombre del catálogo", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("capability-i1")).toBeTruthy());
    expect(screen.getByTestId("capability-i1").textContent).toContain("Jira MCP");
    // Una instalación deshabilitada no se puede desplegar (el backend da 409):
    // ofrecerla sería prometer algo que va a fallar.
    expect(screen.queryByTestId("capability-i2")).toBeNull();
  });

  it("marcar una capacidad abre su formulario con defaults y targets pre-marcados", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("capability-check-i1")).toBeTruthy());
    expect(screen.queryByTestId("capability-i1-form")).toBeNull();

    fireEvent.click(screen.getByTestId("capability-check-i1"));
    await waitFor(() => expect(screen.getByTestId("capability-i1-form")).toBeTruthy());
    expect((screen.getByTestId("capability-i1-field-timeout_ms") as HTMLInputElement).value).toBe(
      "30000",
    );
    expect((screen.getByTestId("capability-i1-role-backend_dev") as HTMLInputElement).checked).toBe(
      true,
    );

    // Desmarcar cierra el formulario y descarta su borrador.
    fireEvent.click(screen.getByTestId("capability-check-i1"));
    await waitFor(() => expect(screen.queryByTestId("capability-i1-form")).toBeNull());
  });

  it("un tenant sin nada instalado ve una explicación, no una lista vacía", async () => {
    wireApi({ installations: [], listings: [] });
    mount();
    await waitFor(() => expect(screen.getByTestId("capabilities-empty")).toBeTruthy());
    expect(screen.queryByTestId("capabilities-list")).toBeNull();
  });

  it("señala los errores de config de una capacidad marcada", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("capability-check-i1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("capability-check-i1"));
    await waitFor(() => expect(screen.getByTestId("capability-i1-form")).toBeTruthy());

    fireEvent.change(screen.getByTestId("capability-i1-field-timeout_ms"), {
      target: { value: "0" },
    });
    expect(screen.getByTestId("capability-i1-errors").textContent).toContain("timeout_ms");
  });
});
