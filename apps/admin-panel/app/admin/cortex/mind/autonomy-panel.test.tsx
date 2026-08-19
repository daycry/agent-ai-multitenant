// @vitest-environment jsdom
//
// Córtex F4 / ADR 0078 — la tarjeta de autonomía del Panel de Mente: el
// KILL-SWITCH y el BUDGET de curiosidad.
//
// Por qué existe este fichero. Los helpers puros (`lib/cortex-curiosity.ts`) sí
// tenían tests, y la tarjeta «Lo que está aprendiendo» también (`page.test.tsx`).
// Las dos piezas que la aceptación de la casilla nombra por su `data-testid`
// —`cortex-budget-usage` y `cortex-autonomy-toggle`— NO tenían ninguno: se podían
// borrar del componente sin que la suite se pusiera roja. Es exactamente el
// patrón del §5 de docs/03-guides/verificar-antes-de-implementar.md («mecanismo
// entregado, cero llamantes»), sólo que en su versión de test: helper probado,
// pantalla sin probar.
//
// Lo que se clava aquí, y por qué importa cada cosa:
//
//   1. **El budget llega a la pantalla.** `budgetUsageLabel` ya estaba probado en
//      aislamiento; lo que nadie afirmaba es que el panel lo llame con los dos
//      números del endpoint (`searches_today`, `searches_cap`) y en el idioma
//      activo. Un panel que enseñe el cap donde va el consumido es indistinguible
//      de uno correcto para el helper.
//   2. **El toggle llama al ENDPOINT del kill-switch**, no sólo se pinta. Y con
//      el campo correcto: el panel tiene TRES botones (`autonomy_enabled`,
//      `web_enabled`, `browser_enabled`) contra la MISMA ruta, así que un
//      cable cruzado apagaría la web creyendo apagar la autonomía —y la UI
//      seguiría pareciendo correcta—. Por eso el test mira el cuerpo del PUT,
//      no que hubo un PUT.
//   3. **El copy honesto (ADR 0075 §6) se ve en ES y EN.** Es obligatorio y no
//      removible; sin test se pierde en el primer refactor de la tarjeta.
//
// El componente se monta SOLO (no la página entera): el `page.tsx` es de otro
// carril y montar la página mete cinco endpoints más de ruido entre la
// aserción y lo que se quiere probar.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { AutonomyPanel } from "@/app/admin/cortex/mind/autonomy-panel";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";
const AUTONOMY_PATH = "/owner/cortex/autonomy";

/** Respuesta base de `GET /owner/cortex/autonomy` (el shape que fija el schema). */
function autonomyState(overrides: Record<string, unknown> = {}) {
  return {
    autonomy_enabled: false,
    web_enabled: false,
    browser_enabled: false,
    curiosity_drive_threshold: 0.7,
    circuit_breaker_open: false,
    budget: { searches_today: 3, searches_cap: 10 },
    // El backend manda SIEMPRE las dos notas (`schemas/cortex_autonomy.py`).
    note_es:
      "El córtex investiga temas por su cuenta dentro de límites de coste que tú" +
      " controlas; es un comportamiento programado, no curiosidad consciente.",
    note_en:
      "The cortex researches topics on its own within cost limits you control; this" +
      " is a programmed behaviour, not conscious curiosity.",
    ...overrides,
  };
}

/**
 * Cablea el `apiFetch` mockeado. El GET devuelve `state`; el PUT devuelve el
 * estado con el parche aplicado, que es lo que hace el backend real (y lo que
 * permite comprobar que la pantalla refleja el cambio).
 */
function wireApi(state: Record<string, unknown> = autonomyState()) {
  let current = state;
  apiFetchMock.mockImplementation((path: string, options?: { method?: string; body?: unknown }) => {
    if (path !== AUTONOMY_PATH) return Promise.reject(new Error(`ruta inesperada: ${path}`));
    if ((options?.method ?? "GET") === "PUT") {
      current = { ...current, ...(options?.body as Record<string, unknown>) };
      return Promise.resolve(current);
    }
    return Promise.resolve(current);
  });
}

function mount(lang: "es" | "en" = "es") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <AutonomyPanel />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

/** Espera a que la tarjeta haya cargado (deja de estar en el estado «Cargando…»). */
async function loaded(testid: string) {
  return waitFor(() => screen.getByTestId(testid));
}

/** El cuerpo del último `PUT /owner/cortex/autonomy`, o `undefined` si no hubo. */
function lastPutBody(): Record<string, unknown> | undefined {
  const puts = apiFetchMock.mock.calls.filter(
    ([path, options]) => path === AUTONOMY_PATH && options?.method === "PUT",
  );
  return puts.at(-1)?.[1]?.body as Record<string, unknown> | undefined;
}

// Mismo motivo que en `page.test.tsx`: los `waitFor` esperan transiciones de
// TanStack Query y 1 s se queda corto con la suite corriendo en paralelo.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("tarjeta de budget de curiosidad (`cortex-budget-usage`)", () => {
  it("enseña consumido, cap y porcentaje con los números del endpoint", async () => {
    wireApi();
    mount("es");

    const budget = await loaded("cortex-budget-usage");
    // 3 de 10 ⇒ 30 %. Los dos números vienen del GET, no de un default del
    // componente: si el panel los intercambiara diría "10 de 3".
    expect(budget.textContent).toBe("3 de 10 búsquedas hoy (30 %)");
  });

  it("habla el idioma del panel (el aviso de coste no puede quedarse en ES)", async () => {
    wireApi();
    mount("en");

    const budget = await waitFor(() => {
      const el = screen.getByTestId("cortex-budget-usage");
      expect(el.textContent).toContain("searches today");
      return el;
    });
    expect(budget.textContent).toBe("3 of 10 searches today (30%)");
    expect(budget.textContent).not.toContain("búsquedas");
  });

  it("sin cupo configurado lo DICE, en vez de insinuar que no hay límite", async () => {
    // Es el caso peligroso: un cap a 0 con un "0 %" tranquilizador le diría al
    // owner que le queda presupuesto cuando lo que pasa es que no hay ninguno
    // configurado. El helper ya distingue los dos casos; aquí se comprueba que
    // la pantalla llega a ese camino con los datos reales del endpoint.
    wireApi(autonomyState({ budget: { searches_today: 7, searches_cap: 0 } }));
    mount("es");

    const budget = await loaded("cortex-budget-usage");
    expect(budget.textContent).toBe("7 búsquedas hoy · sin cupo configurado");
  });
});

describe("toggle del kill-switch (`cortex-autonomy-toggle`)", () => {
  it("al pulsarlo hace PUT al endpoint de autonomía con `autonomy_enabled`", async () => {
    wireApi(autonomyState({ autonomy_enabled: false }));
    mount("es");

    const toggle = await loaded("cortex-autonomy-toggle");
    expect(toggle.textContent).toContain("Encender autonomía");
    // Antes de tocar nada NO se ha escrito: sólo se ha leído el estado.
    expect(lastPutBody()).toBeUndefined();

    fireEvent.click(toggle);

    await waitFor(() => expect(lastPutBody()).toBeTruthy());
    expect(lastPutBody()).toEqual({ autonomy_enabled: true });
    // La ruta importa tanto como el cuerpo: es el kill-switch, no otro gate.
    const put = apiFetchMock.mock.calls.find(([, o]) => o?.method === "PUT");
    expect(put?.[0]).toBe(AUTONOMY_PATH);
  });

  it("NO toca los otros dos gates: apagar la autonomía no apaga la web ni el navegador", async () => {
    // Los tres botones pegan contra la MISMA ruta con un update PARCIAL, así que
    // un cable cruzado es invisible salvo que se mire el cuerpo. Y el daño no es
    // simétrico: apagar `web_enabled` creyendo apagar la autonomía deja los
    // bucles de fondo corriendo (ADR 0078) mientras el owner cree haberlos parado.
    wireApi(autonomyState({ autonomy_enabled: true, web_enabled: true, browser_enabled: true }));
    mount("es");

    const toggle = await loaded("cortex-autonomy-toggle");
    expect(toggle.textContent).toContain("Apagar autonomía");

    fireEvent.click(toggle);

    await waitFor(() => expect(lastPutBody()).toBeTruthy());
    const body = lastPutBody()!;
    expect(body).toEqual({ autonomy_enabled: false });
    expect(body).not.toHaveProperty("web_enabled");
    expect(body).not.toHaveProperty("browser_enabled");
  });

  it("refleja el estado nuevo que devuelve el backend (no el que supuso la UI)", async () => {
    wireApi(autonomyState({ autonomy_enabled: false }));
    mount("es");

    const toggle = await loaded("cortex-autonomy-toggle");
    expect(screen.getByTestId("cortex-autonomy-state").textContent).toContain("APAGADA");

    fireEvent.click(toggle);

    await waitFor(() =>
      expect(screen.getByTestId("cortex-autonomy-state").textContent).toContain("ENCENDIDA"),
    );
    expect(screen.getByTestId("cortex-autonomy-toggle").textContent).toContain("Apagar autonomía");
    // Y sólo se escribió UNA vez: el `onSuccess` siembra la caché en vez de
    // re-preguntar, así que un segundo PUT sería un doble flip del kill-switch.
    const puts = apiFetchMock.mock.calls.filter(([, o]) => o?.method === "PUT");
    expect(puts).toHaveLength(1);
  });

  it("un fallo del PUT se dice, y el estado NO se pinta como cambiado", async () => {
    // Mentir aquí es caro: el owner cree haber apagado los bucles autónomos y
    // se va. El botón debe seguir ofreciendo «Encender» y el estado, APAGADA.
    apiFetchMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (path !== AUTONOMY_PATH) return Promise.reject(new Error(`ruta inesperada: ${path}`));
      if ((options?.method ?? "GET") === "PUT") return Promise.reject(new Error("boom"));
      return Promise.resolve(autonomyState({ autonomy_enabled: false }));
    });
    mount("es");

    const toggle = await loaded("cortex-autonomy-toggle");
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(screen.getByTestId("cortex-autonomy-panel").textContent).toContain(
        "No se pudo cambiar el estado",
      ),
    );
    expect(screen.getByTestId("cortex-autonomy-state").textContent).toContain("APAGADA");
  });
});

describe("copy honesto de la autonomía (ADR 0075 §6)", () => {
  it("en castellano avisa de que es un comportamiento programado", async () => {
    wireApi();
    mount("es");

    const note = await loaded("cortex-autonomy-note");
    expect(note.textContent).toContain("comportamiento programado, no curiosidad consciente");
  });

  it("en inglés dice lo mismo traducido (no se queda en ES)", async () => {
    wireApi();
    mount("en");

    const note = await waitFor(() => {
      const el = screen.getByTestId("cortex-autonomy-note");
      expect(el.textContent).toContain("programmed behaviour");
      return el;
    });
    expect(note.textContent).toContain("not conscious curiosity");
    expect(note.textContent).not.toContain("comportamiento programado");
  });

  it("si el backend no manda la nota, el respaldo la pone igual — en los dos idiomas", async () => {
    // El aviso NO es removible (ADR 0075 §6). Un `note_es`/`note_en` vacío
    // —backend viejo, respuesta recortada— dejaba el `<p>` en blanco: la tarjeta
    // seguía enseñando el kill-switch y el gasto SIN el aviso que los explica.
    wireApi(autonomyState({ note_es: "", note_en: "" }));
    const { unmount } = mount("es");
    expect((await loaded("cortex-autonomy-note")).textContent).toContain(
      "comportamiento programado, no curiosidad consciente",
    );
    unmount();

    apiFetchMock.mockReset();
    wireApi(autonomyState({ note_es: "", note_en: "" }));
    mount("en");
    const note = await waitFor(() => {
      const el = screen.getByTestId("cortex-autonomy-note");
      expect(el.textContent).toBeTruthy();
      return el;
    });
    expect(note.textContent).toContain("programmed behaviour, not conscious curiosity");
  });
});
