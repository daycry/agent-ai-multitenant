// @vitest-environment jsdom
// Córtex F4 (Sub-fase 4.5) — la tarjeta «Lo que está aprendiendo» con su gate.
//
// Lo que este test añade sobre `app/admin/cortex/mind/page.test.tsx` (que ya fija
// que la lista se pinta): las DOS piezas que faltaban del plan.
//
//   1. **El gate de aprobación del owner** (ADR 0078): mientras el bucle deja un
//      pursuit en `selected` sin decisión, el owner tiene que poder aprobarlo o
//      rechazarlo. Lo que se clava aquí es el último tramo — que el botón exista,
//      que pegue a `POST /owner/cortex/curiosity/pursuits/{id}/approve` con el
//      `approved` correcto, y que desaparezca en cuanto la decisión está tomada
//      (doble aprobación = doble gasto).
//   2. **El copy honesto en los DOS idiomas**: la API devuelve `note_es` y
//      `note_en`; la pantalla renderizaba sólo la española.
//
// El endpoint `/approve` lo está construyendo el carril de backend; aquí se
// programa contra su contrato (body `{approved: boolean}`), así que el aserto es
// sobre la LLAMADA, no sobre la respuesta real del servidor.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { LearningPanel } from "@/components/cortex/learning-panel";
import { LanguageProvider } from "@/lib/lang-context";
import type { CortexPursuit } from "@/lib/cortex";

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

function pursuit(overrides: Partial<CortexPursuit> = {}): CortexPursuit {
  return {
    id: "pu-1",
    topic: "compilación incremental en Rust",
    status: "digested",
    created_at: "2026-07-20T10:00:00Z",
    surfaced_at: null,
    learning_memory_id: null,
    search_count: 3,
    ...overrides,
  };
}

const HONESTY = {
  note_es: "Comportamiento programado con topes de coste; no es curiosidad consciente.",
  note_en: "Programmed behaviour under cost caps; not conscious curiosity.",
};

function mount(
  props: Partial<React.ComponentProps<typeof LearningPanel>> = {},
  { lang }: { lang?: "es" | "en" } = {},
) {
  if (lang) window.localStorage.setItem("admin-panel.lang", lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <LearningPanel pursuits={[pursuit()]} isLoading={false} isError={false} {...props} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("LearningPanel — gate de aprobación del owner (ADR 0078)", () => {
  it("ofrece Aprobar/Rechazar sólo mientras la decisión está sin tomar", async () => {
    mount({
      pursuits: [
        pursuit({ id: "pending", status: "selected", approved: null }),
        pursuit({ id: "decided", status: "selected", approved: true }),
        pursuit({ id: "running", status: "searching" }),
      ],
    });
    await waitFor(() => expect(screen.getAllByTestId("cortex-pursuit-approve")).toHaveLength(1));
    expect(screen.getAllByTestId("cortex-pursuit-reject")).toHaveLength(1);
    // Y el que espera es el que lo dice.
    expect(screen.getByTestId("cortex-pursuit-pending-pending")).toBeTruthy();
  });

  it("Aprobar llama al endpoint del gate con approved=true", async () => {
    apiFetchMock.mockResolvedValue({});
    mount({ pursuits: [pursuit({ id: "pu-42", status: "selected", approved: null })] });

    fireEvent.click(screen.getByTestId("cortex-pursuit-approve"));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    const [path, options] = apiFetchMock.mock.calls[0] as [
      string,
      { method: string; body: unknown },
    ];
    expect(path).toBe("/owner/cortex/curiosity/pursuits/pu-42/approve");
    expect(options.method).toBe("POST");
    expect(options.body).toEqual({ approved: true });
  });

  it("Rechazar usa el MISMO endpoint con approved=false (no un DELETE inventado)", async () => {
    apiFetchMock.mockResolvedValue({});
    mount({ pursuits: [pursuit({ id: "pu-9", status: "selected", approved: null })] });

    fireEvent.click(screen.getByTestId("cortex-pursuit-reject"));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    const [path, options] = apiFetchMock.mock.calls[0] as [
      string,
      { method: string; body: unknown },
    ];
    expect(path).toBe("/owner/cortex/curiosity/pursuits/pu-9/approve");
    expect(options.body).toEqual({ approved: false });
  });

  it("un fallo del gate se dice, no se traga en silencio", async () => {
    apiFetchMock.mockRejectedValue(new Error("api 500: boom"));
    mount({ pursuits: [pursuit({ id: "pu-1", status: "selected", approved: null })] });

    fireEvent.click(screen.getByTestId("cortex-pursuit-approve"));

    await waitFor(() => expect(screen.getByTestId("cortex-pursuit-decide-error")).toBeTruthy());
  });
});

describe("LearningPanel — estados y copy honesto bilingüe", () => {
  it("traduce el estado del ciclo de vida y nunca enseña el slug", () => {
    mount({ pursuits: [pursuit({ status: "digested" })] });
    const panel = screen.getByTestId("cortex-learning-panel");
    expect(panel.textContent).toContain("aprendido — pendiente de contarlo");
    expect(panel.textContent).not.toContain("digested");
  });

  it("en EN muestra la nota inglesa de la API, no la castellana", async () => {
    mount({ honesty: HONESTY }, { lang: "en" });
    await waitFor(() =>
      expect(screen.getByTestId("cortex-learning-honesty").textContent).toContain(
        "not conscious curiosity",
      ),
    );
    expect(screen.getByTestId("cortex-learning-honesty").textContent).not.toContain(
      "curiosidad consciente",
    );
  });

  it("en ES muestra la castellana", async () => {
    mount({ honesty: HONESTY }, { lang: "es" });
    await waitFor(() =>
      expect(screen.getByTestId("cortex-learning-honesty").textContent).toContain(
        "no es curiosidad consciente",
      ),
    );
  });

  it("sin nota de la API el aviso NO desaparece (ADR 0075 §6: es obligatorio)", () => {
    mount({ honesty: undefined });
    expect(screen.getByTestId("cortex-learning-honesty").textContent).toMatch(
      /no es curiosidad consciente/i,
    );
  });

  it("el estado vacío y el error son estados distintos", () => {
    const { unmount } = mount({ pursuits: [] });
    expect(screen.getByTestId("cortex-learning-panel").textContent).toContain("Aún no hay temas");
    unmount();

    mount({ pursuits: [], isError: true });
    expect(screen.getByTestId("cortex-learning-panel").textContent).toContain(
      "No se pudo cargar el historial de curiosidad",
    );
    expect(screen.getByTestId("cortex-learning-panel").textContent).not.toContain(
      "Aún no hay temas",
    );
  });

  it("muestra el coste de la pasada cuando el backend lo manda, y no un 0 inventado", () => {
    // `cost_usd` está en la tabla pero el bucle todavía no lo escribe (hueco de
    // F4 en el backend): mientras llegue ausente, la UI NO debe pintar "0,00 $"
    // como si el dato fuera real.
    const { unmount } = mount({ pursuits: [pursuit({ cost_usd: 0.0123 })] });
    expect(screen.getByTestId("cortex-learning-panel").textContent).toContain("0.0123");
    unmount();

    mount({ pursuits: [pursuit()] });
    expect(screen.queryByTestId("cortex-pursuit-cost")).toBeNull();
  });
});
