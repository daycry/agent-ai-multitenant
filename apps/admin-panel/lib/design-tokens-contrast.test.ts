/**
 * ui-refresh-refactor, test humano human_ui_01: «contraste legible (modo oscuro)».
 *
 * El plan pedía ajustar la paleta «sin romper contraste/accesibilidad» y lo dejó
 * en un ojo humano. Esto lo convierte en una medida: parsea los tokens HSL de
 * `app/globals.css`, los convierte a sRGB y calcula el ratio de contraste WCAG
 * 2.1 de los pares texto/fondo que la UI REALMENTE empareja, exigiendo AA
 * (4.5:1 para texto normal).
 *
 * Dos decisiones que este fichero hace explícitas, para que no envejezca:
 *
 *  1. **Se mide `:root`, no `.dark`.** `tailwind.config.ts` usa
 *     `darkMode: ["class"]` y NINGÚN elemento del panel lleva la clase `dark`
 *     (`app/layout.tsx` monta `<html lang="en">` a secas): el tema que se sirve
 *     es el violeta de `:root`, que YA es oscuro. Medir `.dark` sería medir
 *     código inalcanzable. La guarda de abajo se cae si alguien cablea el
 *     toggle, para que entonces se extienda esta medición.
 *  2. **Los pares son los que se usan.** Verificado por grep: `text-success-
 *     foreground`, `text-info-foreground` y `text-danger-foreground` NO aparecen
 *     en ningún `.tsx`, así que medir `success-foreground / success` mediría una
 *     combinación que nadie pinta — un rojo que no protege a ningún usuario.
 *
 * Hallazgo VIVO: `destructive-foreground / destructive` (el botón destructivo,
 * `components/ui/button.tsx`) da 4.40:1 — por debajo de AA. Está en
 * `KNOWN_BELOW_AA` con su valor medido, NO silenciado: el test exige que siga
 * midiéndose, que no empeore, y que se quite del mapa el día que se arregle
 * (`--destructive: 0 70% 52%` da 4.73:1). El fichero de tokens vive fuera de
 * este carril, de ahí que se reporte en vez de arreglarse aquí.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(fileURLToPath(new URL("../app/globals.css", import.meta.url)), "utf8");

// ---------------------------------------------------------------------------
// Parseo de tokens + WCAG
// ---------------------------------------------------------------------------

interface Hsl {
  h: number;
  s: number;
  l: number;
}

/** Cuerpo del bloque `selector { … }` (balanceando llaves anidadas). */
function cssBlock(selector: string): string {
  const start = CSS.indexOf(selector);
  if (start === -1) throw new Error(`no existe el bloque ${selector} en globals.css`);
  const open = CSS.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < CSS.length; i++) {
    if (CSS[i] === "{") depth++;
    else if (CSS[i] === "}") {
      depth--;
      if (depth === 0) return CSS.slice(open + 1, i);
    }
  }
  throw new Error(`bloque ${selector} sin cerrar`);
}

/** `--name: H S% L%;` → { name: {h,s,l} }. Ignora los tokens no-HSL. */
function parseTokens(body: string): Record<string, Hsl> {
  const out: Record<string, Hsl> = {};
  const re = /--([a-z-]+):\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%\s*;/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) {
    out[m[1]] = { h: Number(m[2]), s: Number(m[3]), l: Number(m[4]) };
  }
  return out;
}

/** HSL → sRGB en [0,1] (misma fórmula que el navegador para `hsl()`). */
export function hslToSrgb({ h, s, l }: Hsl): [number, number, number] {
  const S = s / 100;
  const L = l / 100;
  const c = (1 - Math.abs(2 * L - 1)) * S;
  const hp = (((h % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let rgb: [number, number, number];
  if (hp < 1) rgb = [c, x, 0];
  else if (hp < 2) rgb = [x, c, 0];
  else if (hp < 3) rgb = [0, c, x];
  else if (hp < 4) rgb = [0, x, c];
  else if (hp < 5) rgb = [x, 0, c];
  else rgb = [c, 0, x];
  const m = L - c / 2;
  return [rgb[0] + m, rgb[1] + m, rgb[2] + m];
}

/** Luminancia relativa WCAG 2.1 (§ relative luminance). */
export function relativeLuminance(rgb: [number, number, number]): number {
  const lin = rgb.map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

/** Ratio de contraste WCAG 2.1 entre dos colores (1..21). */
export function contrastRatio(a: Hsl, b: Hsl): number {
  const la = relativeLuminance(hslToSrgb(a));
  const lb = relativeLuminance(hslToSrgb(b));
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// ---------------------------------------------------------------------------
// Los pares que la UI empareja de verdad (fg, bg) + dónde
// ---------------------------------------------------------------------------
const PAIRS: [fg: string, bg: string, where: string][] = [
  ["foreground", "background", "body (app/layout.tsx)"],
  ["card-foreground", "card", "Card"],
  ["popover-foreground", "popover", "popovers/combobox"],
  ["muted-foreground", "background", "texto secundario sobre la página"],
  ["muted-foreground", "card", "texto secundario dentro de una Card"],
  ["muted-foreground", "muted", "Badge default/muted"],
  ["primary-foreground", "primary", "Button default / burbuja de usuario"],
  ["destructive-foreground", "destructive", "Button destructive"],
  ["success-soft-foreground", "success-soft", "Badge success"],
  ["warning-soft-foreground", "warning-soft", "Badge warning"],
  ["danger-soft-foreground", "danger-soft", "Badge danger"],
  ["info-soft-foreground", "info-soft", "Badge info"],
  ["sidebar-foreground", "sidebar", "sidebar"],
  ["sidebar-muted-foreground", "sidebar", "etiquetas de grupo del sidebar"],
];

const AA_NORMAL = 4.5;

/**
 * Pares medidos que HOY no llegan a AA, con el ratio medido como suelo.
 * No es una excepción silenciosa: el test exige que sigan por debajo (si se
 * arreglan, hay que sacarlos de aquí) y que no empeoren.
 */
const KNOWN_BELOW_AA: Record<string, number> = {
  // `--destructive: 0 70% 55%` con texto blanco. Fix propuesto: bajar a 52%
  // (4.73:1). `app/globals.css` está fuera de este carril.
  "destructive-foreground/destructive": 4.4,
};

const ROOT = parseTokens(cssBlock(":root"));

/**
 * Los pares por debajo del umbral, formateados. Extraído para poder demostrar
 * —abajo, con un juego de tokens sintético— que el detector SÍ marca en rojo:
 * una guarda que nunca se ha visto fallar no es una guarda (docs/03-guides/
 * verificar-antes-de-implementar.md §4).
 */
export function findOffenders(
  tokens: Record<string, Hsl>,
  pairs: [string, string, string][],
  threshold: number,
): string[] {
  const offenders: string[] = [];
  for (const [fg, bg, where] of pairs) {
    const a = tokens[fg];
    const b = tokens[bg];
    if (!a || !b) continue;
    const ratio = contrastRatio(a, b);
    if (ratio < threshold) offenders.push(`${fg}/${bg} = ${ratio.toFixed(2)}:1 (${where})`);
  }
  return offenders;
}

describe("Tokens de color — contraste WCAG AA en el tema servido (:root)", () => {
  it("todos los tokens de los pares medidos existen y son HSL", () => {
    // Sin esto un token renombrado haría que su par se SALTARA en silencio y el
    // resto del bloque pasaría en vacío.
    const missing = PAIRS.filter(([fg, bg]) => !ROOT[fg] || !ROOT[bg]).map(
      ([fg, bg]) => `${fg}/${bg}`,
    );
    expect(missing).toEqual([]);
    expect(PAIRS.length).toBeGreaterThanOrEqual(12);
  });

  it("cada par texto/fondo llega a 4.5:1", () => {
    const measurable = PAIRS.filter(([fg, bg]) => !(`${fg}/${bg}` in KNOWN_BELOW_AA));
    // La guarda encontró algo que medir (no pasa en vacío).
    expect(measurable.length).toBeGreaterThanOrEqual(12);
    expect(findOffenders(ROOT, measurable, AA_NORMAL)).toEqual([]);
  });

  it("el detector marca en rojo un par real por debajo de AA", () => {
    // Prueba de que el rojo es alcanzable con los tokens REALES del fichero, no
    // solo con aritmética de juguete: `success-foreground` (blanco) sobre
    // `success` (emerald-400) da 1.77:1. Ese par NO está en PAIRS a propósito —
    // `text-success-foreground` no aparece en ningún .tsx, así que la UI no lo
    // pinta — pero sirve para ver el detector fallar.
    const offenders = findOffenders(
      ROOT,
      [["success-foreground", "success", "par sintético de control"]],
      AA_NORMAL,
    );
    expect(offenders).toHaveLength(1);
    expect(offenders[0]).toContain("success-foreground/success");
  });

  it("los pares tolerados siguen por debajo de AA y no han empeorado", () => {
    for (const [key, floor] of Object.entries(KNOWN_BELOW_AA)) {
      const [fg, bg] = key.split("/");
      expect(ROOT[fg], `token ${fg} desaparecido`).toBeDefined();
      expect(ROOT[bg], `token ${bg} desaparecido`).toBeDefined();
      const ratio = contrastRatio(ROOT[fg], ROOT[bg]);
      // Si se ARREGLÓ, este mapa miente: quítalo de KNOWN_BELOW_AA.
      expect(
        ratio,
        `${key} ya cumple AA (${ratio.toFixed(2)}:1): sácalo de KNOWN_BELOW_AA`,
      ).toBeLessThan(AA_NORMAL);
      // Y mientras siga tolerado, que no empeore.
      expect(ratio, `${key} empeoró a ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(floor);
    }
  });

  it("el tema medido es el que se sirve: nadie cablea la clase `dark`", () => {
    // Premisa de la que depende medir SOLO `:root`. Si alguien enciende el
    // toggle de tema, este test se cae y toca extender la medición a `.dark`
    // (hoy `primary-foreground/primary` daría allí 4.03:1).
    expect(CSS).toContain(".dark");
    const layout = readFileSync(
      fileURLToPath(new URL("../app/layout.tsx", import.meta.url)),
      "utf8",
    );
    expect(layout).not.toMatch(/className="[^"]*\bdark\b/);
    expect(layout).not.toMatch(/classList\.(add|toggle)\(\s*["']dark["']/);
  });
});

describe("La matemática de contraste es la de WCAG (calibración del medidor)", () => {
  // Un medidor mal calibrado convierte el test de arriba en teatro.
  const WHITE: Hsl = { h: 0, s: 0, l: 100 };
  const BLACK: Hsl = { h: 0, s: 0, l: 0 };

  it("blanco sobre negro = 21:1 y un color contra sí mismo = 1:1", () => {
    expect(contrastRatio(WHITE, BLACK)).toBeCloseTo(21, 5);
    expect(contrastRatio(WHITE, WHITE)).toBeCloseTo(1, 5);
  });

  it("es simétrico (el orden fg/bg no cambia el ratio)", () => {
    const a: Hsl = { h: 263, s: 83, l: 58 };
    expect(contrastRatio(a, WHITE)).toBeCloseTo(contrastRatio(WHITE, a), 10);
  });

  it("convierte HSL a sRGB como el navegador", () => {
    expect(hslToSrgb({ h: 0, s: 100, l: 50 })).toEqual([1, 0, 0]);
    expect(hslToSrgb({ h: 120, s: 100, l: 50 })).toEqual([0, 1, 0]);
    expect(hslToSrgb({ h: 240, s: 100, l: 50 })).toEqual([0, 0, 1]);
    expect(hslToSrgb({ h: 0, s: 0, l: 50 }).map((v) => Number(v.toFixed(3)))).toEqual([
      0.5, 0.5, 0.5,
    ]);
  });

  it("el gris medio sobre blanco ronda el 3.9:1 conocido", () => {
    // #808080 sobre blanco = 3.95:1 (valor de referencia publicado).
    expect(contrastRatio({ h: 0, s: 0, l: 50.2 }, WHITE)).toBeCloseTo(3.95, 1);
  });
});
