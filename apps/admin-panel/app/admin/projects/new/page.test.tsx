// @vitest-environment jsdom
//
// El wizard de proyecto encadena los despliegues del marketplace tras crear
// (ADR 0142, `task_mkt2_07`).
//
// Lo que este test protege es una decisión, no un detalle:
//
//   * **NO se toca `POST /projects`.** El wizard crea el proyecto y luego
//     encadena un POST por capacidad. Si algún día alguien mete los despliegues
//     dentro de la creación, el assert del cuerpo de `POST /projects` lo canta.
//   * **Un despliegue que falla no aborta la creación** ni se traga: el proyecto
//     existe, el resultado se enseña por-item y hay salida hacia él.
//   * **Sin nada instalado el wizard sigue teniendo dos pasos.** El paso nuevo
//     no puede cobrar peaje a quien no usa el marketplace.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
}));

import NewProjectWizardPage from "@/app/admin/projects/new/page";
import { LanguageProvider } from "@/lib/lang-context";

const INSTALLATIONS = [{ id: "i1", listing_id: "l1", version: "1.2.0", status: "enabled" }];
const LISTINGS = [
  {
    id: "l1",
    kind: "mcp_server",
    name: "Jira MCP",
    version: "1.2.0",
    description: null,
    trust_level: "verified",
    manifest: {
      targets: ["backend_dev"],
      config_schema: {
        properties: { base_url: { type: "string", title: "Base URL", default: null } },
        required: [],
      },
    },
  },
];

function wireApi({
  installations = INSTALLATIONS as Record<string, unknown>[],
  listings = LISTINGS as Record<string, unknown>[],
  deployFails = false,
} = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects" && opts?.method === "POST") {
      return Promise.resolve({ id: "proj-new", name: "Nuevo" });
    }
    if (path.includes("/deployments") && opts?.method === "POST") {
      if (deployFails) return Promise.reject(new Error("boom"));
      return Promise.resolve({
        deployment: { id: "dep-1" },
        already_deployed: false,
        warnings: ["el servidor aún no tiene tools importadas"],
        oauth_pending: true,
      });
    }
    if (path.startsWith("/marketplace/installations")) return Promise.resolve(installations);
    if (path.startsWith("/marketplace/listings")) return Promise.resolve(listings);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // El wizard usa `useLang()` (no la variante opcional) para el label del
  // runtime, así que necesita el provider real.
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <NewProjectWizardPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

/** Paso 1 → 2 con un proyecto en blanco y nombre puesto. */
async function fillBlankProject() {
  await waitFor(() => expect(screen.getByTestId("wizard-blank-project-pick")).toBeTruthy());
  fireEvent.click(screen.getByTestId("wizard-blank-project-pick"));
  fireEvent.change(screen.getByTestId("wizard-name"), { target: { value: "Nuevo" } });
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  pushMock.mockReset();
});

describe("wizard de proyecto — paso «Capacidades»", () => {
  it("sin nada instalado el wizard sigue siendo de dos pasos y crea directamente", async () => {
    wireApi({ installations: [], listings: [] });
    mount();
    await fillBlankProject();

    expect(screen.queryByTestId("wizard-next")).toBeNull();
    fireEvent.click(screen.getByTestId("wizard-submit"));
    await waitFor(() => expect(pushMock).toHaveBeenCalledWith("/admin/projects?created=proj-new"));
  });

  it("con capacidades instaladas aparece el tercer paso y ofrece lo del tenant", async () => {
    wireApi();
    mount();
    await fillBlankProject();

    await waitFor(() => expect(screen.getByTestId("wizard-next")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-next"));
    await waitFor(() => expect(screen.getByTestId("wizard-step-3")).toBeTruthy());
    expect(screen.getByTestId("capability-i1").textContent).toContain("Jira MCP");
  });

  it("crea el proyecto SIN tocar su API y encadena el despliegue después", async () => {
    wireApi();
    mount();
    await fillBlankProject();
    await waitFor(() => expect(screen.getByTestId("wizard-next")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-next"));

    await waitFor(() => expect(screen.getByTestId("capability-check-i1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("capability-check-i1"));
    fireEvent.change(screen.getByTestId("capability-i1-field-base_url"), {
      target: { value: "https://jira.example" },
    });
    fireEvent.click(screen.getByTestId("wizard-submit"));

    await waitFor(() => {
      const create = apiFetchMock.mock.calls.find(
        ([p, o]) => p === "/projects" && (o as { method?: string })?.method === "POST",
      );
      expect(create).toBeTruthy();
      // La creación NO lleva capacidades: ésa es la decisión que se protege.
      expect(Object.keys(create?.[1]?.body ?? {})).not.toContain("marketplace_installations");
      expect(Object.keys(create?.[1]?.body ?? {})).not.toContain("deployments");
    });

    await waitFor(() => {
      const deploy = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          p === "/marketplace/installations/i1/deployments" &&
          (o as { method?: string })?.method === "POST",
      );
      expect(deploy).toBeTruthy();
      expect(deploy?.[1]?.body).toEqual({
        project_id: "proj-new",
        config: { base_url: "https://jira.example" },
        role_map: ["backend_dev"],
      });
    });

    // Se enseñan avisos y OAuth pendiente, y NO se redirige a ciegas.
    await waitFor(() => expect(screen.getByTestId("wizard-deploy-results")).toBeTruthy());
    expect(screen.getByTestId("wizard-deploy-warnings-i1")).toBeTruthy();
    expect(screen.getByTestId("wizard-deploy-oauth-i1")).toBeTruthy();
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("wizard-goto-project")).toBeTruthy();
  });

  it("un despliegue que falla no borra que el proyecto SÍ se creó", async () => {
    wireApi({ deployFails: true });
    mount();
    await fillBlankProject();
    await waitFor(() => expect(screen.getByTestId("wizard-next")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-next"));
    await waitFor(() => expect(screen.getByTestId("capability-check-i1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("capability-check-i1"));
    fireEvent.click(screen.getByTestId("wizard-submit"));

    await waitFor(() =>
      expect(screen.getByTestId("wizard-deploy-result-i1").getAttribute("data-outcome")).toBe(
        "failed",
      ),
    );
    // El proyecto existe: hay salida hacia él aunque el despliegue fallara.
    expect(screen.getByTestId("wizard-goto-project")).toBeTruthy();
  });

  it("no deja crear mientras la config de una capacidad marcada no valide", async () => {
    wireApi({
      listings: [
        {
          ...LISTINGS[0],
          manifest: {
            targets: [],
            config_schema: {
              properties: { base_url: { type: "string", title: "Base URL" } },
              required: ["base_url"],
            },
          },
        },
      ],
    });
    mount();
    await fillBlankProject();
    await waitFor(() => expect(screen.getByTestId("wizard-next")).toBeTruthy());
    fireEvent.click(screen.getByTestId("wizard-next"));
    await waitFor(() => expect(screen.getByTestId("capability-check-i1")).toBeTruthy());

    expect((screen.getByTestId("wizard-submit") as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByTestId("capability-check-i1"));
    await waitFor(() => expect(screen.getByTestId("capability-i1-errors")).toBeTruthy());
    expect((screen.getByTestId("wizard-submit") as HTMLButtonElement).disabled).toBe(true);
  });
});
