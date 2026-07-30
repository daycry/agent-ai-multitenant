// @vitest-environment jsdom
// ui-refresh-refactor, test humano human_ui_01: «los estados vacío/cargando/error
// son consistentes en TODAS las pantallas».
//
// `StateBlock` es el componente que hace cierto ese enunciado: lo usan decenas de
// pantallas (usuarios, comandos del proyecto, KBs…) y no tenía NI UN test. Es el
// peor sitio posible para no tener red: su contrato es una PRECEDENCIA
// (loading → error → empty → children) y un fallo de precedencia se ve como
// "una pantalla que muestra 'no hay nada' mientras carga" — el modo de fallo que
// hace desconfiar del dato.

import { cleanup, render, screen } from "@testing-library/react";
import { Boxes } from "lucide-react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { StateBlock } from "@/components/shared/state-block";

afterEach(cleanup);

function children() {
  return <div data-testid="rows">filas</div>;
}

describe("StateBlock — precedencia loading → error → empty → children", () => {
  it("sin ninguna bandera renderiza los children tal cual", () => {
    render(<StateBlock>{children()}</StateBlock>);
    expect(screen.getByTestId("rows")).toBeTruthy();
  });

  it("cargando gana a error y a vacío (no se pinta 'no hay nada' mientras carga)", () => {
    render(
      <StateBlock
        isLoading
        isError
        isEmpty
        error={new Error("boom")}
        loadingTestId="l"
        errorTestId="e"
        emptyTestId="v"
      >
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("l")).toBeTruthy();
    expect(screen.queryByTestId("e")).toBeNull();
    expect(screen.queryByTestId("v")).toBeNull();
    expect(screen.queryByTestId("rows")).toBeNull();
  });

  it("error gana a vacío: un fallo no se disfraza de lista vacía", () => {
    // Es la distinción que más engaña: "no hay datos" y "no pude preguntar" se
    // leen igual si el error cae al estado vacío.
    render(
      <StateBlock isError isEmpty error={new Error("boom")} errorTestId="e" emptyTestId="v">
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("e")).toBeTruthy();
    expect(screen.queryByTestId("v")).toBeNull();
    expect(screen.queryByTestId("rows")).toBeNull();
  });

  it("vacío sustituye a los children, no se pintan los dos", () => {
    render(
      <StateBlock isEmpty emptyTestId="v" emptyTitle="No hay equipos">
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("v")).toBeTruthy();
    expect(screen.getByTestId("v").textContent).toContain("No hay equipos");
    expect(screen.queryByTestId("rows")).toBeNull();
  });
});

describe("StateBlock — el estado de carga se anuncia a un lector de pantalla", () => {
  it("la línea con spinner lleva aria-busy y aria-live", () => {
    render(
      <StateBlock isLoading loadingTestId="l" loadingLabel="Cargando usuarios…">
        {children()}
      </StateBlock>,
    );
    const el = screen.getByTestId("l");
    expect(el.textContent).toContain("Cargando usuarios…");
    expect(el.getAttribute("aria-busy")).toBe("true");
    expect(el.getAttribute("aria-live")).toBe("polite");
  });

  it("el modo skeleton pinta N filas y también se anuncia", () => {
    render(
      <StateBlock isLoading loadingSkeleton skeletonRows={4} loadingTestId="l">
        {children()}
      </StateBlock>,
    );
    const el = screen.getByTestId("l");
    expect(el.getAttribute("aria-busy")).toBe("true");
    // Las filas son decorativas (aria-hidden) pero deben ser TANTAS como se pidió:
    // un skeleton de 1 fila donde se pidieron 4 miente sobre el tamaño de la lista.
    expect(el.querySelectorAll('[aria-hidden="true"]')).toHaveLength(4);
  });

  it("por defecto el skeleton trae 3 filas", () => {
    render(
      <StateBlock isLoading loadingSkeleton loadingTestId="l">
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("l").querySelectorAll('[aria-hidden="true"]')).toHaveLength(3);
  });
});

describe("StateBlock — el error dice QUÉ pasó", () => {
  it("prefiere el `body` de un ApiError sobre el message genérico", () => {
    // ApiError.message es "api 404: <body>"; el body es el texto útil.
    const apiError = { body: "plan no encontrado", message: "api 404: plan no encontrado" };
    render(
      <StateBlock isError error={apiError} errorTestId="e">
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("e").textContent).toContain("plan no encontrado");
  });

  it("cae al message cuando no hay body", () => {
    render(
      <StateBlock isError error={new Error("network down")} errorTestId="e">
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("e").textContent).toContain("network down");
  });

  it("un error sin texto no deja el bloque en blanco", () => {
    render(
      <StateBlock isError error={null} errorTestId="e">
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("e").textContent).toContain("Error desconocido");
  });

  it("el bloque de error es un role=alert (se anuncia solo)", () => {
    render(
      <StateBlock isError error={new Error("boom")} errorTestId="e">
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("e").getAttribute("role")).toBe("alert");
  });
});

describe("StateBlock — el vacío es sustituible pieza a pieza", () => {
  it("acepta icono, título, descripción y acción", () => {
    render(
      <StateBlock
        isEmpty
        emptyTestId="v"
        emptyIcon={Boxes}
        emptyTitle="Sin proyectos"
        emptyDescription="Crea el primero para empezar."
        emptyAction={<button data-testid="cta">Crear</button>}
      >
        {children()}
      </StateBlock>,
    );
    const el = screen.getByTestId("v");
    expect(el.textContent).toContain("Sin proyectos");
    expect(el.textContent).toContain("Crea el primero para empezar.");
    expect(screen.getByTestId("cta")).toBeTruthy();
    expect(el.querySelector("svg")).not.toBeNull();
  });

  it("`empty` reemplaza el bloque entero (los pases sueltos se ignoran)", () => {
    render(
      <StateBlock
        isEmpty
        emptyTestId="v"
        emptyTitle="NO DEBERÍA VERSE"
        empty={<p data-testid="custom">mi propio vacío</p>}
      >
        {children()}
      </StateBlock>,
    );
    expect(screen.getByTestId("custom")).toBeTruthy();
    expect(screen.getByTestId("v").textContent).not.toContain("NO DEBERÍA VERSE");
  });
});
