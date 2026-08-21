// @vitest-environment jsdom
// Córtex F4 / ADR 0078 — la tarjeta «Lo que está aprendiendo» del Panel de Mente.
//
// El fetcher (`getCortexPursuits`) tiene sus tests en `lib/cortex.test.ts`; lo que
// NADIE afirmaba es que el dato llegue a la pantalla. Este repo tiene un patrón
// documentado de fallo exactamente ahí (docs/03-guides/verificar-antes-de-
// implementar.md §5: «mecanismo entregado, cero llamantes»): un endpoint que
// alguien consume y ninguna vista renderiza. Aquí se clava el último tramo:
//
//   - la tarjeta existe y pinta UNA fila por pursuit, con su tema;
//   - el estado del ciclo de vida se traduce a copy legible (nunca el slug);
//   - sin temas se muestra el estado vacío, y un 403 se distingue del vacío;
//   - el copy honesto (bucle programado, no curiosidad consciente) está presente
//     — ADR 0075 §6 lo exige y sin test se cae en el primer refresco de UI.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/ws", () => ({
  useWebSocket: () => {},
  wsUrl: (p: string) => `ws://test${p}`,
}));

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isSystemOwner: true,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import { ApiError } from "@/lib/api";
import CortexMindPage from "@/app/admin/cortex/mind/page";

const MIND = {
  valence: 0.2,
  arousal: 0.4,
  dominance: 0.1,
  intensity: 0.3,
  mood_valence: 0.2,
  mood_arousal: 0.4,
  mood_dominance: 0.1,
  mood_label: "sereno",
  drives: { curiosity: 0.7, bonding: 0.5, coherence: 0.6, competence: 0.5 },
  honesty: { note_es: "Simulación determinista.", note_en: "Deterministic simulation." },
};

const PURSUITS = [
  {
    id: "pu-1",
    topic: "compilación incremental en Rust",
    status: "digested",
    created_at: "2026-07-20T10:00:00Z",
    surfaced_at: null,
    learning_memory_id: "mem-1",
    search_count: 3,
  },
  {
    id: "pu-2",
    topic: "pgvector HNSW vs IVFFlat",
    status: "surfaced",
    created_at: "2026-07-21T10:00:00Z",
    surfaced_at: "2026-07-22T09:00:00Z",
    learning_memory_id: "mem-2",
    search_count: 5,
  },
];

function wireApi({ pursuits = PURSUITS as unknown[], pursuitsError = false } = {}) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/owner/cortex/mind") return Promise.resolve(MIND);
    if (path.startsWith("/owner/cortex/curiosity/pursuits")) {
      return pursuitsError
        ? Promise.reject(new ApiError(403, "forbidden"))
        : Promise.resolve(pursuits);
    }
    // timeseries / episodes / journal / autonomy / browse-sessions: vacíos.
    if (path.startsWith("/owner/cortex/autonomy")) {
      return Promise.resolve({
        autonomy_enabled: false,
        web_enabled: false,
        browser_enabled: false,
        curiosity_drive_threshold: 0.7,
        circuit_breaker_open: false,
        budget: { searches_today: 0, searches_cap: 10 },
        note_es: "",
        note_en: "",
      });
    }
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CortexMindPage />
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

describe("Panel de Mente — tarjeta «Lo que está aprendiendo» (ADR 0078)", () => {
  it("renderiza una fila por pursuit con su tema y su estado legible", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-learning-panel")).toBeTruthy());
    const panel = screen.getByTestId("cortex-learning-panel");
    expect(panel.textContent).toContain("Lo que está aprendiendo");

    const list = await waitFor(() => screen.getByTestId("cortex-learning-list"));
    expect(list.querySelectorAll("li")).toHaveLength(2);
    expect(list.textContent).toContain("compilación incremental en Rust");
    expect(list.textContent).toContain("pgvector HNSW vs IVFFlat");

    // El estado del ciclo de vida se traduce; el slug crudo no se enseña.
    expect(list.textContent).toContain("aprendido — pendiente de contarlo");
    expect(list.textContent).toContain("comentado en conversación");
    expect(list.textContent).not.toContain("digested");
    expect(list.textContent).not.toContain("surfaced");

    // Y el fetcher se llamó con el límite que declara la pantalla.
    const call = apiFetchMock.mock.calls.find(
      ([p]) => typeof p === "string" && p.includes("/curiosity/pursuits"),
    );
    expect(call?.[0]).toContain("limit=20");
  });

  it("lleva el copy honesto: el bucle es programado, no curiosidad consciente", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-learning-panel")).toBeTruthy());
    expect(screen.getByTestId("cortex-learning-panel").textContent).toContain(
      "no es curiosidad consciente",
    );
  });

  it("sin temas muestra el estado vacío, no una lista vacía", async () => {
    wireApi({ pursuits: [] });
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-learning-panel")).toBeTruthy());
    await waitFor(() =>
      expect(screen.getByTestId("cortex-learning-panel").textContent).toContain("Aún no hay temas"),
    );
    expect(screen.queryByTestId("cortex-learning-list")).toBeNull();
  });

  it("un fallo del endpoint se distingue del estado vacío", async () => {
    // «No hay temas» y «no pude preguntar» son estados distintos: pintar el vacío
    // ante un 403 le dice al owner que el córtex no aprende nada, que es falso.
    wireApi({ pursuitsError: true });
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-learning-panel")).toBeTruthy());
    await waitFor(() =>
      expect(screen.getByTestId("cortex-learning-panel").textContent).toContain(
        "No se pudo cargar el historial de curiosidad",
      ),
    );
    expect(screen.getByTestId("cortex-learning-panel").textContent).not.toContain(
      "Aún no hay temas",
    );
  });
});
