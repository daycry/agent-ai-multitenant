/**
 * Tests de la guarda `check-component-size.mjs` (plan prod-16, `task_prod16_08`).
 *
 * El plan pedía "un guard que falle si algún `page.tsx` supera 800 líneas, para
 * que la deuda no vuelva a crecer". Igual que con `check-i18n`, lo que hay que
 * comprobar de una guarda no es que pase, sino que SEPA FALLAR: una guarda que
 * no puede fallar no es una guarda (verificar-antes-de-implementar §4).
 *
 * El trinquete es el mismo que el de i18n: allowlist de la deuda conocida que
 * sólo puede MENGUAR, fichero nuevo por encima del límite = error, fichero de
 * la allowlist que CRECE = error, y `--strict` para el día que la allowlist se
 * vacíe.
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

const APP_ROOT = resolve(__dirname, "..");
const SCRIPT = join(APP_ROOT, "scripts", "check-component-size.mjs");

const fixtures: string[] = [];

afterEach(() => {
  for (const dir of fixtures.splice(0)) rmSync(dir, { recursive: true, force: true });
});

/** Un fichero de `n` líneas. */
function lines(n: number): string {
  return `${"const x = 1;\n".repeat(n - 1)}const x = 1;`;
}

function fixture(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), "check-size-"));
  fixtures.push(root);
  for (const [rel, content] of Object.entries(files)) {
    const abs = join(root, rel);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, content, "utf8");
  }
  return root;
}

/**
 * Una entrada REAL de la allowlist de la guarda, con su tamaño anotado.
 *
 * Los fixtures de abajo necesitan un fichero que la allowlist conozca, y antes
 * ese nombre estaba clavado a mano (`llm-providers`). El día que se partió de
 * verdad —el movimiento que la guarda existe para premiar— cuatro tests de este
 * fichero se pusieron rojos por el éxito, y el fallo no se leía como "actualiza
 * el fixture" sino como "la guarda está rota". Se lee de la propia guarda para
 * que no vuelva a pasar.
 */
function anAllowlistedScreen(): { rel: string; allowed: number } {
  const raw = execFileSync(process.execPath, [SCRIPT, "--print-allowlist"], {
    encoding: "utf8",
  });
  const parsed = JSON.parse(raw) as { screens: Record<string, number> };
  const entries = Object.entries(parsed.screens);
  // Si la allowlist se vacía (deuda saldada, enhorabuena), estos tests dejan de
  // tener sujeto: mejor un fallo con este mensaje que un `undefined` opaco.
  expect(entries.length).toBeGreaterThan(0);
  const [rel, allowed] = entries[0];
  return { rel, allowed };
}

/** Lo mismo para la allowlist de PIEZAS (secciones, diálogos, pestañas…). */
function aSectionInTheAllowlist(): { rel: string; allowed: number } {
  const raw = execFileSync(process.execPath, [SCRIPT, "--print-allowlist"], {
    encoding: "utf8",
  });
  const parsed = JSON.parse(raw) as { sections: Record<string, number> };
  const entries = Object.entries(parsed.sections);
  expect(entries.length).toBeGreaterThan(0);
  const [rel, allowed] = entries[0];
  return { rel, allowed };
}

function run(args: string[]): { code: number; output: string } {
  try {
    const stdout = execFileSync(process.execPath, [SCRIPT, ...args], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { code: 0, output: stdout };
  } catch (err) {
    const e = err as { status?: number; stdout?: string; stderr?: string };
    return { code: e.status ?? 1, output: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

describe("check-component-size sobre el árbol real", () => {
  it("pasa: la deuda actual está anotada en la allowlist", () => {
    const { code, output } = run([]);

    expect(output).toContain("check-component-size OK");
    expect(code).toBe(0);
  });

  it("recorre de verdad el panel (si viera 3 ficheros pasaría en vacío)", () => {
    const scanned = Number(/check-component-size: (\d+) ficheros/.exec(run([]).output)?.[1] ?? 0);

    expect(scanned).toBeGreaterThan(100);
  });

  it("informa de cuántos siguen por encima del límite (no puede ser cero hoy)", () => {
    const over = Number(/(\d+) por encima de/.exec(run([]).output)?.[1] ?? -1);

    // Guarda contra el paso en vacío: si el descubrimiento se rompiera, este
    // número caería a 0 y la guarda diría que la deuda está saldada.
    expect(over).toBeGreaterThan(0);
  });
});

describe("check-component-size sabe fallar", () => {
  it("un page.tsx NUEVO por encima del límite es error", () => {
    const root = fixture({ "app/admin/nuevo/page.tsx": lines(801) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("app/admin/nuevo/page.tsx");
    expect(output).toContain("801");
  });

  it("un fichero de la allowlist que CRECE es error", () => {
    // Anotado con su tamaño actual; con 200 líneas más debe saltar.
    const { rel, allowed } = anAllowlistedScreen();
    const root = fixture({ [rel]: lines(allowed + 200) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("La deuda no puede crecer");
  });

  it("respeta --max-lines: con un límite bajo, un fichero pequeño ya infringe", () => {
    const root = fixture({ "app/admin/nuevo/page.tsx": lines(120) });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--max-lines", "100"]).code).toBe(1);
  });

  it("--strict no perdona ni a los de la allowlist", () => {
    // Por encima del límite pero dentro de lo anotado: sin --strict pasa.
    const { rel, allowed } = anAllowlistedScreen();
    const root = fixture({ [rel]: lines(allowed) });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });
});

describe("check-component-size no molesta donde no debe", () => {
  it("un page.tsx por debajo del límite pasa", () => {
    const root = fixture({ "app/admin/nuevo/page.tsx": lines(400) });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("un fichero de la allowlist que MENGUA pasa, y avisa para bajar el número", () => {
    // Menos de lo anotado pero aún por encima del límite de 800.
    const { rel, allowed } = anAllowlistedScreen();
    const root = fixture({ [rel]: lines(Math.max(801, allowed - 20)) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("baja el número en la allowlist");
  });

  it("un fichero de la allowlist que baja del límite pasa y pide borrarlo", () => {
    const { rel } = anAllowlistedScreen();
    const root = fixture({ [rel]: lines(300) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("bórralo de la allowlist");
  });

  it("los tests no cuentan: un page.test.tsx enorme no es una pantalla", () => {
    const root = fixture({ "app/admin/nuevo/page.test.tsx": lines(2000) });

    expect(run(["--root", root]).code).toBe(0);
  });
});

/**
 * El agujero que tenía la guarda, y que premiaba el atajo.
 *
 * Sólo medía `page.tsx`. Sacar 700 líneas del `page.tsx` a un
 * `algo-sections.tsx` bajaba el contador sin partir nada — y eso pasó de
 * verdad: `mcp-server-sections.tsx` acabó en 1125 líneas y la guarda daba OK,
 * con el propio comentario del script reconociéndolo ("su tamaño se vigila a
 * ojo en review"). Vigilar a ojo es no vigilar.
 *
 * El techo de las piezas es 500, que es el que el plan fija para las secciones
 * de `model-prices` ("`page.tsx` < 400 líneas, ninguna sección > 500").
 */
describe("check-component-size también mide las piezas del troceado", () => {
  it("una sección NUEVA por encima de 500 es error", () => {
    const root = fixture({ "app/admin/nuevo/cosa-section.tsx": lines(501) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("cosa-section.tsx");
  });

  it("cubre las cuatro formas de nombrar una pieza, no sólo *-section", () => {
    for (const name of ["a-sections.tsx", "b-dialog.tsx", "c-tab.tsx", "d-panel.tsx"]) {
      const root = fixture({ [`app/admin/nuevo/${name}`]: lines(600) });

      expect(run(["--root", root]).code, name).toBe(1);
    }
  });

  it("una pieza por debajo del techo pasa", () => {
    const root = fixture({ "app/admin/nuevo/cosa-section.tsx": lines(499) });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("una pieza de la allowlist dentro de su cupo pasa, y por encima falla", () => {
    const { rel, allowed } = aSectionInTheAllowlist();
    expect(run(["--root", fixture({ [rel]: lines(allowed) })]).code).toBe(0);
    expect(run(["--root", fixture({ [rel]: lines(allowed + 50) })]).code).toBe(1);
  });

  it("--strict tampoco perdona a las piezas de la allowlist", () => {
    const { rel, allowed } = aSectionInTheAllowlist();
    const root = fixture({ [rel]: lines(allowed) });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });

  it("sobre el árbol real informa de cuántas piezas siguen pasadas de tamaño", () => {
    const over = Number(/(\d+) pieza\(s\) por encima de/.exec(run([]).output)?.[1] ?? -1);

    // Igual que con las pantallas: si el descubrimiento se rompiera, esto
    // caería a 0 y la guarda diría que la deuda está saldada.
    expect(over).toBeGreaterThan(0);
  });

  it("un test enorme con nombre de sección no cuenta", () => {
    const root = fixture({ "app/admin/nuevo/cosa-section.test.tsx": lines(2000) });

    expect(run(["--root", root]).code).toBe(0);
  });
});
