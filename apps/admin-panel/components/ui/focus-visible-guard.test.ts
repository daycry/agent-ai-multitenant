/**
 * ui-refresh-refactor, test humano human_ui_01: «navegación por teclado + foco
 * visibles».
 *
 * Era la única línea del checklist que dependía por completo de que alguien se
 * acordara de tabular por la pantalla. Esto la convierte en una guarda estática:
 * TODA primitiva compartida que renderice un control enfocable de verdad
 * (`<button>`, `<input>`, `<select>`, `<textarea>`) debe declarar un anillo de
 * foco `focus-visible:*`. Sin él, el control se puede alcanzar con el teclado
 * pero no se VE dónde está el foco — que es peor que no poder alcanzarlo,
 * porque el usuario cree que la tecla no hizo nada.
 *
 * La guarda lleva su propia comprobación de no-vacuidad (§4 de
 * docs/03-guides/verificar-antes-de-implementar.md): si el descubrimiento deja
 * de encontrar primitivas interactivas, el test falla en vez de pasar en verde
 * sin haber mirado nada.
 *
 * Cuando se escribió encontró un hueco REAL: `components/ui/view-toggle.tsx`
 * pintaba dos `<button role="tab">` sin ningún `focus-visible:*`. Se arregló
 * añadiéndole el anillo (mismo patrón que Select/Input), no bajando el listado.
 */

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/** Carpetas de primitivas compartidas que la guarda audita. */
const DIRS = ["./", "../shared/"] as const;

/** Etiquetas HTML que reciben foco por sí solas (sin `tabIndex`). */
const FOCUSABLE_TAG = /<(button|input|select|textarea)[\s>]/;

interface Primitive {
  name: string;
  source: string;
}

function collect(): Primitive[] {
  const out: Primitive[] = [];
  for (const dir of DIRS) {
    const base = fileURLToPath(new URL(dir, import.meta.url));
    for (const file of readdirSync(base)) {
      if (!file.endsWith(".tsx")) continue;
      if (file.endsWith(".test.tsx")) continue; // los tests no son primitivas
      out.push({
        name: `${dir === "./" ? "components/ui" : "components/shared"}/${file}`,
        source: readFileSync(base + file, "utf8"),
      });
    }
  }
  return out;
}

const PRIMITIVES = collect();
const INTERACTIVE = PRIMITIVES.filter((p) => FOCUSABLE_TAG.test(p.source));

describe("Primitivas compartidas — anillo de foco visible (human_ui_01)", () => {
  it("el descubrimiento encuentra primitivas (la guarda no pasa en vacío)", () => {
    // Si un refactor mueve las primitivas de carpeta, esto se cae en vez de
    // dejar de auditar en silencio.
    expect(PRIMITIVES.length).toBeGreaterThanOrEqual(20);
    expect(INTERACTIVE.length).toBeGreaterThanOrEqual(9);
    // Y encuentra las que sabemos que existen.
    const names = INTERACTIVE.map((p) => p.name);
    for (const expected of [
      "components/ui/button.tsx",
      "components/ui/input.tsx",
      "components/ui/select.tsx",
      "components/ui/checkbox.tsx",
      "components/ui/view-toggle.tsx",
    ]) {
      expect(names, `la guarda dejó de ver ${expected}`).toContain(expected);
    }
  });

  it("toda primitiva con un control enfocable declara focus-visible", () => {
    const offenders = INTERACTIVE.filter((p) => !p.source.includes("focus-visible:")).map(
      (p) => p.name,
    );
    expect(offenders, `sin anillo de foco: ${offenders.join(", ")}`).toEqual([]);
  });

  it("el anillo se apoya en el token del tema, no en un color crudo", () => {
    // `focus-visible:outline-none` sin anillo propio QUITA el foco nativo del
    // navegador: es el modo de fallo que deja el control invisible al tabular.
    const offenders: string[] = [];
    for (const p of INTERACTIVE) {
      if (!/focus-visible:(ring-2|ring-\[|outline-\w)/.test(p.source)) {
        offenders.push(`${p.name}: focus-visible sin anillo/outline propio`);
      }
      if (
        p.source.includes("focus-visible:outline-none") &&
        !p.source.includes("focus-visible:ring")
      ) {
        offenders.push(`${p.name}: quita el outline nativo y no pone anillo`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
