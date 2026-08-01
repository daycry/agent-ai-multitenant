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

/**
 * Una entrada REAL de cada allowlist de la guarda, con su cupo anotado.
 *
 * Los fixtures de abajo necesitan ficheros que las allowlists conozcan. Clavar
 * el nombre a mano hace que cada migración exitosa ponga rojos estos tests: le
 * pasó al guard hermano el 2026-08-01 al partir `llm-providers`, y el fallo se
 * leía como "la guarda está rota" en vez de "actualiza el fixture". Se leen de
 * la propia guarda para que el test siga a la deuda, no a un nombre.
 */
function anAllowlisted(kind: "ternaries" | "attrs"): { rel: string; allowed: number } {
  const raw = execFileSync(process.execPath, [SCRIPT, "--print-allowlist"], {
    encoding: "utf8",
  });
  const parsed = JSON.parse(raw) as Record<string, Record<string, number>>;
  const entries = Object.entries(parsed[kind]);
  // Si la allowlist se vacía (migración terminada, enhorabuena), estos tests se
  // quedan sin sujeto: mejor este mensaje que un `undefined` opaco.
  expect(entries.length).toBeGreaterThan(0);
  const [rel, allowed] = entries[0];
  return { rel, allowed };
}

describe("check-i18n sabe fallar", () => {
  it("un fichero NUEVO con ternario es error", () => {
    const root = fixture({ "app/admin/nuevo/page.tsx": TERNARY });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("app/admin/nuevo/page.tsx");
    expect(output).toContain("useT()");
  });

  it("un fichero de la allowlist con MÁS ternarios de los anotados es error", () => {
    const { rel, allowed } = anAllowlisted("ternaries");
    const root = fixture({ [rel]: TERNARY.repeat(allowed + 1) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain(`la allowlist permite ${allowed}`);
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
    const { rel, allowed } = anAllowlisted("ternaries");
    const root = fixture({ [rel]: TERNARY.repeat(allowed) });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("avisa (sin fallar) cuando un fichero baja de su cupo", () => {
    const { rel, allowed } = anAllowlisted("ternaries");
    // Sólo tiene sentido si el cupo anotado es > 1; los de cupo 1 no pueden bajar
    // sin salir del mapa, y ese caso lo cubre el aviso de "bórralo".
    if (allowed <= 1) return;
    const root = fixture({ [rel]: TERNARY });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("baja el número en la allowlist");
  });
});

/**
 * Segundo trinquete (prod-16 `task_prod16_03`): castellano CABLEADO en atributos
 * que ve el usuario.
 *
 * El de los ternarios sólo pilla los ficheros que ya traducían a mano. El grueso
 * de la deuda no son ternarios sino literales fijos, y con el toggle en EN se
 * quedan en castellano sin que nada se queje. Este busca los que llevan un
 * carácter que sólo existe en castellano (tildes, ñ, ¿, ¡) dentro de
 * `placeholder`/`aria-label`/`title`/`label`/`description`…: es exacto, no
 * interpreta prosa, y por eso no da falsos positivos.
 */
describe("check-i18n — literales castellanos en atributos de UI", () => {
  // Los DOS atributos llevan un carácter propio del castellano: sin él la guarda
  // no cuenta nada, que es justo lo que comprueba el caso "no adivina idioma".
  const SPANISH_ATTR = '<Input placeholder="Buscar por número…" aria-label="Búsqueda" />\n';

  it("un fichero NUEVO con un atributo en castellano es error", () => {
    const root = fixture({ "app/admin/nuevo/page.tsx": SPANISH_ATTR });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("app/admin/nuevo/page.tsx");
    expect(output).toContain("atributo");
  });

  it("cuenta ocurrencias, no ficheros", () => {
    const root = fixture({ "app/admin/nuevo/page.tsx": SPANISH_ATTR });

    // `placeholder` + `aria-label` en la misma línea son dos.
    expect(run(["--root", root]).output).toContain("2 atributo(s)");
  });

  it("un atributo sin caracteres propios del castellano no cuenta (no adivina idioma)", () => {
    const root = fixture({ "app/x.tsx": '<Input placeholder="Search by name" />\n' });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("no mira atributos que el usuario no lee (className, data-testid, href)", () => {
    const root = fixture({
      "app/x.tsx": '<a className="año" data-testid="día" href="/ñ">x</a>\n',
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("un fichero de la allowlist dentro de su cupo pasa, y por encima falla", () => {
    // Arrastra deuda anotada; con MUCHOS más debe fallar.
    const { rel, allowed } = anAllowlisted("attrs");
    const root = fixture({ [rel]: SPANISH_ATTR.repeat(allowed + 50) });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("La deuda no puede crecer");
  });

  it("las pantallas ya migradas están a cero y el trinquete las protege", () => {
    // Si alguien vuelve a cablear castellano en `users`, esto salta: el fichero
    // no está en la allowlist de atributos.
    const root = fixture({ "app/admin/users/page.tsx": SPANISH_ATTR });

    expect(run(["--root", root]).code).toBe(1);
  });

  it("--strict no perdona tampoco los atributos de la allowlist", () => {
    const { rel } = anAllowlisted("attrs");
    const root = fixture({ [rel]: SPANISH_ATTR });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });

  it("sobre el árbol real informa de cuánta deuda de atributos queda", () => {
    const { output } = run([]);

    const pending = Number(/(\d+) atributo\(s\) pendientes/.exec(output)?.[1] ?? -1);
    // Guarda contra el paso en vacío: hoy quedan ~141. Si el patrón dejara de
    // encontrar nada, este número caería a 0 y el aviso sería una mentira.
    expect(pending).toBeGreaterThan(50);
  });
});

describe("check-i18n --strict", () => {
  it("no perdona ni lo que la allowlist permitía", () => {
    const root = fixture({ "components/capability/persona-section.tsx": TERNARY.repeat(2) });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });

  it("sigue respetando la exención de lib/i18n/", () => {
    const root = fixture({ "lib/i18n/dictionary.ts": TERNARY });

    expect(run(["--root", root, "--strict"]).code).toBe(0);
  });
});
