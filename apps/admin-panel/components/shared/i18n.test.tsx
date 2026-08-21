// @vitest-environment jsdom

/**
 * `components/shared/`, migrado al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Este fichero cubre la forma nº 2 de la deuda en su versión más cara: un
 * componente COMPARTIDO sin migrar que montan pantallas ya migradas. `StateBlock`
 * es el caso extremo: lo importan **21 ficheros** de `app/` —usuarios, equipos,
 * proyectos, ajustes, agentes, herramientas, el hub del proyecto…— y sus tres
 * textos por defecto («Cargando…», «Sin resultados», «No se pudo cargar») estaban
 * cableados en castellano. Con el toggle en EN, veintiuna pantallas «migradas»
 * seguían diciendo «Cargando…» mientras cargaban.
 *
 * Ninguna de las dos guardas lo veía:
 *
 *   * `check-i18n.mjs` mide FICHEROS, no pantallas, y encima aquí veía **cero
 *     atributos**: los tres literales son valores por defecto de props
 *     (`loadingLabel = "Cargando…"`), no atributos JSX.
 *   * El trinquete de ternarios tampoco: no hay ni un `lang === "es"`.
 *
 * Y el caso hermano, que es el contrario: `form-section.tsx` figuraba con **4
 * atributos** y `list-toolbar.tsx` con **1**, y los cinco viven dentro del
 * ejemplo de un JSDoc de dos componentes que NO monta ninguna pantalla. El
 * contador miente en las dos direcciones a la vez.
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { DataTable } from "@/components/shared/data-table";
import { ListToolbar } from "@/components/shared/list-toolbar";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError } from "@/lib/api";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  window.localStorage.setItem(STORAGE_KEY, lang);
  return render(<LanguageProvider>{node}</LanguageProvider>);
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("StateBlock — los tres textos por defecto salen del diccionario", () => {
  it.each([
    ["es", "Cargando"],
    ["en", "Loading"],
  ] as const)("la línea de carga en %s dice «%s»", (lang, expected) => {
    renderIn(
      lang,
      <StateBlock isLoading loadingTestId="l">
        <div />
      </StateBlock>,
    );
    expect(screen.getByTestId("l").textContent).toContain(expected);
  });

  it("con el toggle en EN la línea de carga NO dice «Cargando»", () => {
    renderIn(
      "en",
      <StateBlock isLoading loadingTestId="l">
        <div />
      </StateBlock>,
    );
    expect(screen.getByTestId("l").textContent).not.toContain("Cargando");
  });

  it.each([
    ["es", "Sin resultados"],
    ["en", "No results"],
  ] as const)("el vacío por defecto en %s dice «%s»", (lang, expected) => {
    renderIn(
      lang,
      <StateBlock isEmpty emptyTestId="v">
        <div />
      </StateBlock>,
    );
    expect(screen.getByTestId("v").textContent).toContain(expected);
  });

  it("con el toggle en EN el vacío NO dice «Sin resultados»", () => {
    renderIn(
      "en",
      <StateBlock isEmpty emptyTestId="v">
        <div />
      </StateBlock>,
    );
    expect(screen.getByTestId("v").textContent).not.toContain("Sin resultados");
  });

  it.each([
    ["es", "No se pudo cargar"],
    ["en", "Could not load"],
  ] as const)("el título de error en %s dice «%s»", (lang, expected) => {
    renderIn(
      lang,
      <StateBlock isError error={new ApiError(500, "")} errorTestId="e">
        <div />
      </StateBlock>,
    );
    expect(screen.getByTestId("e").textContent).toContain(expected);
  });
});

describe("StateBlock — el cuerpo crudo del backend NO llega a pantalla", () => {
  // La razón de ser de `task_prod16_05`. `StateBlock` tenía su PROPIA copia del
  // helper (`errorMessage`), con el defecto de las otras quince: `return e.body`.
  // Al montarlo 21 pantallas, era la copia con más alcance de todas.
  it("un 500 con HTML de nginx se sustituye por el mensaje traducido", () => {
    renderIn(
      "es",
      <StateBlock isError error={new ApiError(500, "<html>nginx traceback</html>")} errorTestId="e">
        <div />
      </StateBlock>,
    );
    const text = screen.getByTestId("e").textContent ?? "";
    expect(text).not.toContain("nginx");
    expect(text).not.toContain("<html>");
    expect(text).toContain("El servidor ha fallado");
  });

  it("tampoco por la vía del `message`, que es `api {status}: {body}`", () => {
    // `ApiError.message` NO es un mensaje: es el cuerpo crudo con un prefijo.
    // Leerlo «porque es un Error normal» reintroduce la fuga por la puerta de
    // atrás, y es la vía que el censo de `task_prod16_05` no contemplaba.
    const err = new ApiError(502, "upstream connect error");
    expect(err.message).toBe("api 502: upstream connect error");
    renderIn(
      "en",
      <StateBlock isError error={err} errorTestId="e">
        <div />
      </StateBlock>,
    );
    const text = screen.getByTestId("e").textContent ?? "";
    expect(text).not.toContain("api 502");
    expect(text).not.toContain("upstream connect error");
  });

  it("el `detail` legible del backend SÍ se enseña (no se pierde información)", () => {
    renderIn(
      "es",
      <StateBlock
        isError
        error={new ApiError(404, JSON.stringify({ detail: "plan no encontrado" }))}
        errorTestId="e"
      >
        <div />
      </StateBlock>,
    );
    const text = screen.getByTestId("e").textContent ?? "";
    expect(text).toContain("plan no encontrado");
    // Sin estas dos, la aserción de arriba la cumplía YA el código defectuoso:
    // pintar el cuerpo crudo `{"detail":"plan no encontrado"}` también «contiene»
    // la frase. Lo que distingue leer el cuerpo de mostrarlo es lo que NO sale.
    expect(text).not.toContain("detail");
    expect(text).not.toContain("{");
  });

  it("un error sin nada legible cae al texto traducido, no a «[object Object]»", () => {
    renderIn(
      "en",
      <StateBlock isError error={null} errorTestId="e">
        <div />
      </StateBlock>,
    );
    const text = screen.getByTestId("e").textContent ?? "";
    expect(text).toContain("An unexpected error occurred");
    expect(text).not.toContain("Error desconocido");
    expect(text).not.toContain("[object Object]");
  });
});

describe("DataTable — la fila de «no hay nada» sale del diccionario", () => {
  // Su literal (`emptyMessage = "Sin resultados."`) es un valor por defecto, no
  // un atributo, así que `check-i18n` le veía CERO. No estaba ni en la
  // allowlist: no era deuda «pendiente», era deuda invisible.
  it.each([
    ["es", "Sin resultados"],
    ["en", "No results"],
  ] as const)("en %s dice «%s»", (lang, expected) => {
    renderIn(
      lang,
      <DataTable
        data={[]}
        columns={[{ key: "a", header: "A", cell: () => null }]}
        data-testid="t"
      />,
    );
    expect(screen.getByTestId("t").textContent).toContain(expected);
  });
});

describe("ListToolbar — el buscador por defecto sale del diccionario", () => {
  it.each([
    ["es", "Buscar"],
    ["en", "Search"],
  ] as const)("en %s el placeholder dice «%s»", (lang, expected) => {
    renderIn(lang, <ListToolbar search="" onSearchChange={() => {}} searchTestId="s" />);
    expect(screen.getByTestId("s").getAttribute("placeholder")).toContain(expected);
  });

  it("el aria-label cae al placeholder traducido, no al castellano fijo", () => {
    renderIn("en", <ListToolbar search="" onSearchChange={() => {}} searchTestId="s" />);
    expect(screen.getByTestId("s").getAttribute("aria-label")).not.toContain("Buscar");
  });
});
