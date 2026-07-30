// @vitest-environment jsdom
// ui-refresh-refactor (task_ui_02): la primitiva `EmptyState`, que el plan añadió
// para que "los estados vacío/cargando/error sean consistentes en todas las
// pantallas" (human_ui_01), no tenía NINGÚN test.
//
// Lo que se clava aquí es su contrato mínimo: el título siempre se ve, lo opcional
// solo cuando se pasa, el icono es decorativo (no lo lee un lector de pantalla),
// y los atributos del llamante (`data-testid`, `aria-*`, `id`) LLEGAN al DOM —
// eso último es lo que permite a `StateBlock` y a los e2e apuntar al bloque.

import { cleanup, render, screen } from "@testing-library/react";
import { Inbox } from "lucide-react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { EmptyState } from "@/components/ui/empty-state";

afterEach(cleanup);

describe("EmptyState", () => {
  it("renderiza el título (lo único obligatorio)", () => {
    render(<EmptyState title="No hay knowledge bases" data-testid="es" />);
    expect(screen.getByTestId("es").textContent).toContain("No hay knowledge bases");
  });

  it("no inventa descripción, acción ni icono cuando no se pasan", () => {
    render(<EmptyState title="Vacío" data-testid="es" />);
    const el = screen.getByTestId("es");
    // Sin icono no hay svg suelto que ocupe sitio…
    expect(el.querySelector("svg")).toBeNull();
    // …y el bloque contiene SOLO el título.
    expect(el.textContent).toBe("Vacío");
  });

  it("muestra la descripción y la acción cuando se pasan", () => {
    render(
      <EmptyState
        title="No hay proyectos"
        description="Crea el primero desde el wizard."
        action={<button data-testid="cta">Crear proyecto</button>}
        data-testid="es"
      />,
    );
    expect(screen.getByTestId("es").textContent).toContain("Crea el primero desde el wizard.");
    expect(screen.getByTestId("cta")).toBeTruthy();
  });

  it("el icono es DECORATIVO: no se anuncia al lector de pantalla", () => {
    // Un icono anunciado en un estado vacío añade ruido sin información: el
    // texto ya dice lo que pasa.
    render(<EmptyState title="Bandeja vacía" icon={Inbox} data-testid="es" />);
    const disc = screen.getByTestId("es").querySelector('[aria-hidden="true"]');
    expect(disc).not.toBeNull();
    expect(disc?.querySelector("svg")).not.toBeNull();
  });

  it("renderiza también los children, debajo de la acción", () => {
    render(
      <EmptyState title="Vacío" data-testid="es">
        <span data-testid="extra">nota al pie</span>
      </EmptyState>,
    );
    expect(screen.getByTestId("extra")).toBeTruthy();
  });

  it("propaga los atributos del llamante y NO se come su className", () => {
    // `StateBlock` le pasa `className` + `data-testid`; si los descartara, cada
    // pantalla perdería su selector y su ajuste de layout.
    render(
      <EmptyState
        title="Vacío"
        data-testid="es"
        id="mi-vacio"
        aria-label="sin resultados"
        className="mt-9"
      />,
    );
    const el = screen.getByTestId("es");
    expect(el.id).toBe("mi-vacio");
    expect(el.getAttribute("aria-label")).toBe("sin resultados");
    expect(el.className).toContain("mt-9");
    // Y conserva su propio estilo base (borde discontinuo del placeholder).
    expect(el.className).toContain("border-dashed");
  });

  it("expone la ref al div raíz (forwardRef)", () => {
    const ref = React.createRef<HTMLDivElement>();
    render(<EmptyState ref={ref} title="Vacío" data-testid="es" />);
    expect(ref.current).toBe(screen.getByTestId("es"));
  });
});
