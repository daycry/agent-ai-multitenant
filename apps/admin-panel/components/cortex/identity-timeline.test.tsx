// @vitest-environment jsdom
// Córtex F3.6 — radar Big-Five + timeline de versiones de la identidad.
//
// Dos huecos de la auditoría 2026-07-27 en un solo test porque comparten página:
//
//   - los rasgos se pintaban como BARRAS y el plan pedía un RADAR (la forma del
//     perfil no se lee en cinco barras sueltas);
//   - no había NINGUNA UI de timeline de versiones, porque no había endpoint que
//     la alimentara.
//
// El endpoint `GET /owner/cortex/identity/history` lo está construyendo el carril
// de backend; esta UI se programa contra su contrato (versiones con `version`,
// `created_at`, `reason`, `diff`) y trata el 404 como "todavía no está" en vez de
// como un error del owner — de otro modo, hasta que el endpoint aterrice, la
// pantalla acusaría al usuario de un fallo que no es suyo.
//
// NO verificado end-to-end: sin el endpoint desplegado no hay forma de probar el
// camino real; lo que aquí se fija es el contrato y el comportamiento de la UI.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { IdentityTimeline } from "@/components/cortex/identity-timeline";
import { TraitRadar } from "@/components/cortex/trait-radar";
import { ApiError } from "@/lib/api";
import {
  radarPolygon,
  traitRadarAxes,
  TRAIT_LABELS_ES,
  type CortexIdentityVersion,
  type CortexTraits,
} from "@/lib/cortex-identity";

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const TRAITS: CortexTraits = {
  openness: 0.8,
  conscientiousness: 0.6,
  extraversion: 0.3,
  agreeableness: 0.7,
  neuroticism: 0.2,
};

const VERSIONS: CortexIdentityVersion[] = [
  {
    version: 3,
    created_at: "2026-07-26T10:00:00Z",
    updated_by: "reflection",
    reason: "reflexión periódica",
    diff: {
      traits: { before: { openness: 0.5 }, after: { openness: 0.56 } },
      narrative: { before: "algo", after: "otra cosa" },
    },
  },
  {
    version: 2,
    created_at: "2026-07-20T10:00:00Z",
    updated_by: "owner_override",
    reason: null,
    diff: { name: { before: null, after: "Atlas" } },
  },
];

function mountTimeline() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IdentityTimeline />
    </QueryClientProvider>,
  );
}

describe("TraitRadar — el radar Big-Five (F3.6)", () => {
  it("dibuja el polígono con la geometría de la función pura", () => {
    const { container } = render(<TraitRadar traits={TRAITS} />);
    const polygon = container.querySelector('[data-testid="cortex-trait-polygon"]');
    expect(polygon?.getAttribute("points")).toBe(radarPolygon(traitRadarAxes(TRAITS)));
  });

  it("rotula los cinco rasgos (el radar se lee también como texto)", () => {
    render(<TraitRadar traits={TRAITS} />);
    const panel = screen.getByTestId("cortex-trait-radar");
    for (const label of Object.values(TRAIT_LABELS_ES)) {
      expect(panel.textContent).toContain(label);
    }
  });

  it("muestra el valor numérico de cada rasgo, no sólo la forma", () => {
    render(<TraitRadar traits={TRAITS} />);
    expect(screen.getByTestId("cortex-trait-value-openness").textContent).toContain("0.80");
  });

  it("un rasgo fuera de rango no rompe el SVG", () => {
    const { container } = render(
      <TraitRadar traits={{ ...TRAITS, openness: 7, neuroticism: -3 }} />,
    );
    const points = container
      .querySelector('[data-testid="cortex-trait-polygon"]')!
      .getAttribute("points")!;
    expect(points.split(" ")).toHaveLength(5);
    expect(points).not.toContain("NaN");
  });
});

describe("IdentityTimeline — versiones con su diff (F3.6)", () => {
  it("lista las versiones con su resumen legible, sin volcar el JSON del diff", async () => {
    apiFetchMock.mockResolvedValue(VERSIONS);
    mountTimeline();

    await waitFor(() => expect(screen.getByTestId("cortex-identity-timeline-list")).toBeTruthy());
    const list = screen.getByTestId("cortex-identity-timeline-list");
    expect(list.querySelectorAll("li")).toHaveLength(2);
    expect(list.textContent).toContain("versión 3");
    expect(list.textContent).toContain("versión 2");
    expect(list.textContent).toContain("rasgos: 1 ajuste");
    expect(list.textContent).toContain("Atlas");
    expect(list.textContent).not.toContain("{");
    expect(list.textContent).not.toContain("before");
  });

  it("pega al endpoint del histórico con su prefijo owner-only", async () => {
    apiFetchMock.mockResolvedValue([]);
    mountTimeline();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    const path = apiFetchMock.mock.calls[0][0] as string;
    expect(path.startsWith("/owner/cortex/identity/history?")).toBe(true);
    expect(path).toContain("limit=");
  });

  it("muestra el motivo de la reflexión cuando lo hay", async () => {
    apiFetchMock.mockResolvedValue(VERSIONS);
    mountTimeline();
    await waitFor(() =>
      expect(screen.getByTestId("cortex-identity-timeline-list").textContent).toContain(
        "reflexión periódica",
      ),
    );
  });

  it("un 404 dice «todavía no disponible», no acusa al owner de un error", async () => {
    // El endpoint está EN CONSTRUCCIÓN: mientras no exista, el panel tiene que
    // ser honesto sobre por qué está vacío.
    apiFetchMock.mockRejectedValue(new ApiError(404, "Not Found"));
    mountTimeline();
    await waitFor(() =>
      expect(screen.getByTestId("cortex-identity-timeline-pending")).toBeTruthy(),
    );
    expect(screen.queryByTestId("cortex-identity-timeline-error")).toBeNull();
  });

  it("un 500 sí es un error y se dice como tal", async () => {
    apiFetchMock.mockRejectedValue(new ApiError(500, "boom"));
    mountTimeline();
    await waitFor(() => expect(screen.getByTestId("cortex-identity-timeline-error")).toBeTruthy());
  });

  it("sin versiones muestra el estado vacío (identidad recién creada)", async () => {
    apiFetchMock.mockResolvedValue([]);
    mountTimeline();
    await waitFor(() => expect(screen.getByTestId("cortex-identity-timeline-empty")).toBeTruthy());
    expect(screen.queryByTestId("cortex-identity-timeline-list")).toBeNull();
  });

  it("una versión con diff vacío se ve (y dice que no cambió nada observable)", async () => {
    apiFetchMock.mockResolvedValue([
      { version: 5, created_at: "2026-07-27T10:00:00Z", reason: null, diff: {} },
    ]);
    mountTimeline();
    await waitFor(() =>
      expect(screen.getByTestId("cortex-identity-timeline-list").textContent).toContain(
        "sin cambios",
      ),
    );
  });
});
