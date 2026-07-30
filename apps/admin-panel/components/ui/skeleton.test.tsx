// @vitest-environment jsdom
// ui-refresh-refactor (task_ui_02): la primitiva `Skeleton`, sin ningún test.
//
// Su contrato tiene una decisión de accesibilidad que es fácil de romper sin
// darse cuenta: el bloque es DECORATIVO (`aria-hidden`), y quien anuncia "esto
// está cargando" es el contenedor (`StateBlock`, con aria-busy/aria-live). Si el
// skeleton dejara de ser aria-hidden, un lector de pantalla leería N bloques
// vacíos por cada lista en carga.

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { Skeleton } from "@/components/ui/skeleton";

afterEach(cleanup);

describe("Skeleton", () => {
  it("es decorativo: aria-hidden para que no se lea como contenido", () => {
    render(<Skeleton data-testid="sk" />);
    expect(screen.getByTestId("sk").getAttribute("aria-hidden")).toBe("true");
  });

  it("pulsa (es lo que lo distingue de un bloque muerto)", () => {
    render(<Skeleton data-testid="sk" />);
    expect(screen.getByTestId("sk").className).toContain("animate-pulse");
  });

  it("acepta tamaño/forma por className sin perder su estilo base", () => {
    render(<Skeleton data-testid="sk" className="h-12 w-full" />);
    const el = screen.getByTestId("sk");
    expect(el.className).toContain("h-12");
    expect(el.className).toContain("w-full");
    expect(el.className).toContain("animate-pulse");
  });

  it("un className en conflicto gana (cn/twMerge), no se duplica", () => {
    // El contrato de `cn`: la última utilidad Tailwind del mismo grupo gana. Sin
    // esto, `rounded-full` no podría sobreescribir el `rounded-md` base.
    render(<Skeleton data-testid="sk" className="rounded-full" />);
    const el = screen.getByTestId("sk");
    expect(el.className).toContain("rounded-full");
    expect(el.className).not.toContain("rounded-md");
  });

  it("propaga el resto de props al div", () => {
    render(<Skeleton data-testid="sk" id="fila-1" title="cargando" />);
    const el = screen.getByTestId("sk");
    expect(el.id).toBe("fila-1");
    expect(el.getAttribute("title")).toBe("cargando");
  });

  it("expone la ref al div (forwardRef)", () => {
    const ref = React.createRef<HTMLDivElement>();
    render(<Skeleton ref={ref} data-testid="sk" />);
    expect(ref.current).toBe(screen.getByTestId("sk"));
  });
});
