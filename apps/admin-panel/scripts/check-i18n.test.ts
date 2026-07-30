/**
 * Tests de la guarda `check-i18n.mjs` (plan prod-16, `task_prod16_01`).
 *
 * Lo que importa comprobar de una guarda no es que pase, sino que SEPA FALLAR:
 * los cuatro rojos crónicos de `tests/security/` y las guardas que pasaban en
 * vacío son el precedente (docs/03-guides/verificar-antes-de-implementar.md §4).
 * Por eso cada caso se prueba contra un árbol de fixture montado a mano.
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

const APP_ROOT = resolve(__dirname, "..");
const SCRIPT = join(APP_ROOT, "scripts", "check-i18n.mjs");
const TERNARY = 'const label = lang === "es" ? "Hola" : "Hi";\n';

const fixtures: string[] = [];

afterEach(() => {
  for (const dir of fixtures.splice(0)) rmSync(dir, { recursive: true, force: true });
});

/** Árbol temporal con los ficheros indicados (rutas relativas). */
function fixture(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), "check-i18n-"));
  fixtures.push(root);
  for (const [rel, content] of Object.entries(files)) {
    const abs = join(root, rel);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, content, "utf8");
  }
  return root;
}

/** Ejecuta la guarda y devuelve código de salida + salida combinada. */
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

describe("check-i18n sobre el árbol real", () => {
  it("pasa: la deuda actual está anotada en la allowlist", () => {
    const { code, output } = run([]);

    expect(output).toContain("check-i18n OK");
    expect(code).toBe(0);
  });

  it("recorre de verdad el panel (si viera 3 ficheros pasaría en vacío)", () => {
    const scanned = Number(/check-i18n: (\d+) ficheros/.exec(run([]).output)?.[1] ?? 0);

    expect(scanned).toBeGreaterThan(200);
  });
});

describe("check-i18n sabe fallar", () => {
  it("un fichero NUEVO con ternario es error", () => {
    const root = fixture({ "app/admin/nuevo/page.tsx": TERNARY });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("app/admin/nuevo/page.tsx");
    expect(output).toContain("useT()");
  });

  it("un fichero de la allowlist con MÁS ternarios de los anotados es error", () => {
    // La allowlist permite 2 en este fichero; le ponemos 3.
    const root = fixture({ "lib/tools/taxonomy.ts": TERNARY.repeat(3) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("la allowlist permite 2");
    expect(output).toContain("La deuda no puede crecer");
  });

  it("cuenta ocurrencias, no ficheros (dos en el mismo fichero cuentan dos)", () => {
    const root = fixture({ "components/x.tsx": TERNARY.repeat(2) });

    expect(run(["--root", root]).output).toContain("2 ternario(s) pendientes");
  });
});

describe("check-i18n no molesta donde no debe", () => {
  it("un árbol ya migrado pasa", () => {
    const root = fixture({ "app/page.tsx": "export const x = 1;\n" });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("lib/i18n/ está exento: ahí vive el diccionario y su documentación", () => {
    const root = fixture({ "lib/i18n/dictionary.ts": TERNARY });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("un fichero de la allowlist DENTRO de su cupo pasa", () => {
    const root = fixture({ "lib/tools/taxonomy.ts": TERNARY.repeat(2) });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("avisa (sin fallar) cuando un fichero baja de su cupo", () => {
    const root = fixture({ "lib/tools/taxonomy.ts": TERNARY });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("baja el número en la allowlist");
  });
});

describe("check-i18n --strict", () => {
  it("no perdona ni lo que la allowlist permitía", () => {
    const root = fixture({ "lib/tools/taxonomy.ts": TERNARY.repeat(2) });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });

  it("sigue respetando la exención de lib/i18n/", () => {
    const root = fixture({ "lib/i18n/dictionary.ts": TERNARY });

    expect(run(["--root", root, "--strict"]).code).toBe(0);
  });
});
