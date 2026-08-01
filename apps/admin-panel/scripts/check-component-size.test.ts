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
    // `llm-providers` está anotado con su tamaño actual; con 200 líneas más debe saltar.
    const root = fixture({ "app/admin/llm-providers/page.tsx": lines(1200) });

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
    const root = fixture({ "app/admin/llm-providers/page.tsx": lines(900) });

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
    const root = fixture({ "app/admin/llm-providers/page.tsx": lines(850) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("baja el número en la allowlist");
  });

  it("un fichero de la allowlist que baja del límite pasa y pide borrarlo", () => {
    const root = fixture({ "app/admin/llm-providers/page.tsx": lines(300) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("bórralo de la allowlist");
  });

  it("los tests no cuentan: un page.test.tsx enorme no es una pantalla", () => {
    const root = fixture({ "app/admin/nuevo/page.test.tsx": lines(2000) });

    expect(run(["--root", root]).code).toBe(0);
  });
});
