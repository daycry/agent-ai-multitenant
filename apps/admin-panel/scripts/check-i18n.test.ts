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
/** UN atributo en castellano por línea: `.repeat(n)` da exactamente n infractores. */
const SPANISH_ATTR_LINE = '<Input placeholder="Buscar por número…" />\n';

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

  it("el trinquete de ternarios está GRADUADO: cero deuda y allowlist vacía", () => {
    // prod-16 `task_prod16_04`, 2026-08-12: los 63 ternarios de idioma con que
    // nació el plan llegaron a 0. Cuando eso pasa, la allowlist deja de tener
    // sentido y el trinquete pasa de saldar deuda a impedirla — igual que le
    // ocurrió a las pantallas en `check-component-size`.
    const raw = execFileSync(process.execPath, [SCRIPT, "--print-allowlist"], {
      encoding: "utf8",
    });
    const parsed = JSON.parse(raw) as Record<string, Record<string, number>>;

    expect(parsed.ternaries).toEqual({});
    expect(run([]).output).toContain("0 ternario(s) pendientes en 0 fichero(s)");
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
function anAllowlisted(
  kind: "ternaries" | "attrs",
  minAllowed = 1,
): { rel: string; allowed: number } {
  const raw = execFileSync(process.execPath, [SCRIPT, "--print-allowlist"], {
    encoding: "utf8",
  });
  const parsed = JSON.parse(raw) as Record<string, Record<string, number>>;
  // `minAllowed` en vez de coger la primera entrada a secas: un test que mete N
  // infractores en un fichero de la allowlist necesita que ese fichero permita
  // al menos N, o falla por el cupo y no por lo que quería probar. Cogiendo la
  // primera, cada re-baseo del trinquete podía dejarla en 1 y romper tests que
  // no tenían nada que ver — le pasó al re-basar tras afinar la detección.
  const entries = Object.entries(parsed[kind]).filter(([, allowed]) => allowed >= minAllowed);
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

  it("un fichero que ARRASTRABA deuda de ternarios tampoco puede reintroducirla", () => {
    // Con el trinquete graduado ya no hay cupos que respetar: el que fue el
    // mayor bolsón de deuda (`components/capability/`) se juzga como cualquier
    // otro. Antes este caso comprobaba «no más de los N anotados»; hoy el
    // umbral es cero y esto es lo que queda por comprobar.
    const root = fixture({ "components/capability/capability-hub.tsx": TERNARY });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("useT()");
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
    // El sujeto sale ahora de la allowlist de ATRIBUTOS: la de ternarios se
    // vació al cerrar la migración y este caso se quedaría sin nada que probar.
    const { rel, allowed } = anAllowlisted("attrs");
    const root = fixture({ [rel]: SPANISH_ATTR_LINE.repeat(allowed) });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("avisa (sin fallar) cuando un fichero baja de su cupo", () => {
    // Cupo >= 2 para que UN infractor quede por debajo y dispare el aviso.
    const { rel } = anAllowlisted("attrs", 2);
    const root = fixture({ [rel]: SPANISH_ATTR_LINE });

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
    // `2` porque el fixture mete DOS infractores: hace falta un fichero cuyo
    // cupo los admita, o el caso no-estricto fallaría por cupo y no por strict.
    const { rel } = anAllowlisted("attrs", 2);
    const root = fixture({ [rel]: SPANISH_ATTR });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });

  it("sobre el árbol real informa de cuánta deuda de atributos queda", () => {
    const { output } = run([]);

    const pending = Number(/(\d+) atributo\(s\) pendientes/.exec(output)?.[1] ?? -1);
    // Guarda contra el paso en vacío: hoy quedan ~260. Si el patrón dejara de
    // encontrar nada, este número caería a 0 y el aviso sería una mentira.
    expect(pending).toBeGreaterThan(50);
  });
});

/**
 * Tercer bloque: **castellano SIN TILDES** (2026-08-02).
 *
 * El detector de arriba sólo veía caracteres exclusivos del castellano, así que
 * `title="Dar acceso a un proyecto"` pasaba limpio: siete palabras castellanas y
 * ni una tilde. La guarda medía la deuda DETECTABLE, no la deuda, y su número
 * tranquilizaba más de lo que debía.
 *
 * Estos casos fijan las dos mitades del arreglo, y la segunda importa tanto como
 * la primera: una guarda con falsos positivos se desactiva, y entonces no mide
 * nada. Por eso hay tantos casos de "esto NO debe saltar" como de "esto sí".
 */
describe("check-i18n — castellano sin tildes en atributos", () => {
  it("el caso que se le escapaba: siete palabras castellanas, cero tildes", () => {
    const root = fixture({
      "app/admin/nuevo/page.tsx": '<Button title="Dar acceso a un proyecto" />\n',
    });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("app/admin/nuevo/page.tsx");
    expect(output).toContain("atributo");
  });

  it("una etiqueta de UNA palabra castellana también salta", () => {
    // El grueso de los botones del panel: `title="Guardar"`, `title="Eliminar"`.
    // Sin lista de contenido, las palabras sueltas eran invisibles.
    const root = fixture({ "app/x.tsx": '<Button title="Guardar" />\n' });

    expect(run(["--root", root]).code).toBe(1);
  });

  it("los cognados largos los pilla el sufijo, no la lista", () => {
    // `-ciones`, `-idad`, `-miento`… no existen como final de palabra inglesa.
    const root = fixture({
      "app/x.tsx": '<nav aria-label="Notificaciones" title="Seguridad" />\n',
    });

    expect(run(["--root", root]).code).toBe(1);
  });

  it("inglés real del propio panel NO salta (si saltara, se desactivaría la guarda)", () => {
    // Valores literales que ya existen hoy en el panel y son inglés legítimo.
    const root = fixture({
      "app/x.tsx": [
        '<Input placeholder="Search by name" aria-label="API Reference" />',
        '<Spinner loadingLabel="Loading services…" title="Pull request" />',
        '<Tab label="Settings" description="System prompt (EN)" />',
        '<Field label="SP Entity ID" title="Golden login" placeholder="openid email profile" />',
        '<Badge title="Dashboard" label="Breadcrumb" description="Ops bot" />',
      ].join("\n"),
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("identificadores, imágenes y URLs no son prosa castellana", () => {
    // `equipo-plataforma` contiene «equipo», pero es un slug de ejemplo: si la
    // guarda marcase esto, cada placeholder técnico sería un falso positivo.
    const root = fixture({
      "app/x.tsx": [
        '<Input placeholder="qwen2.5-coder:14b" title="event_type" />',
        '<Input placeholder="equipo-plataforma" aria-label="agentic-platform/agent-runtime-php-phpunit:v1" />',
        '<Input placeholder="https://tu-dominio.com" label="vault:secret/data/mcp/<servicio>/<proyecto>" />',
      ].join("\n"),
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("las siglas en mayúsculas no son palabras castellanas", () => {
    // «SE» (sureste), «UN» (Naciones Unidas), «SIN»/«CON» de un enum: en
    // castellano esas palabras van en minúscula. Distinguir por caja cuesta una
    // línea y borra de golpe toda una familia de falsos positivos.
    const root = fixture({
      "app/x.tsx": '<Field label="UN SDK" title="SE" description="SIN / CON" />\n',
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("sigue contando ocurrencias, no ficheros", () => {
    const root = fixture({
      "app/x.tsx": '<Input placeholder="Buscar un agente" aria-label="Cerrar" />\n',
    });

    expect(run(["--root", root]).output).toContain("2 atributo(s)");
  });

  it("una pantalla YA migrada que reintroduce castellano sin tildes salta", () => {
    // Es el escenario que motivó el arreglo: `users` está migrada y fuera de la
    // allowlist, pero la guarda vieja la dejaba reintroducir castellano llano.
    const root = fixture({
      "app/admin/users/page.tsx": '<Button title="Dar acceso a un proyecto" />\n',
    });

    expect(run(["--root", root]).code).toBe(1);
  });
});

describe("check-i18n --strict", () => {
  it("no perdona ni lo que la allowlist permitía", () => {
    // El fichero sale de la allowlist REAL (`anAllowlisted`), no clavado a mano.
    // Estaba clavado a `components/capability/persona-section.tsx` y se puso
    // rojo el día que ese módulo se migró de verdad — el mismo modo de fallo que
    // ya se corrigió dos veces en `check-component-size.test.ts`: un test que se
    // rompe POR EL ÉXITO enseña a desconfiar de la guarda, no del cambio.
    const { rel, allowed } = anAllowlisted("attrs");
    const root = fixture({ [rel]: SPANISH_ATTR_LINE.repeat(allowed) });

    expect(run(["--root", root]).code).toBe(0);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });

  it("para los ternarios da lo MISMO que el modo normal: ya no hay cupo que perdonar", () => {
    // Es la consecuencia observable de graduar el trinquete. Si alguien volviera
    // a meter una entrada en la allowlist de ternarios, los dos modos dejarían
    // de coincidir y este caso lo diría.
    const root = fixture({ "app/admin/lo-que-sea/page.tsx": TERNARY });

    expect(run(["--root", root]).code).toBe(1);
    expect(run(["--root", root, "--strict"]).code).toBe(1);
  });

  it("sigue respetando la exención de lib/i18n/", () => {
    const root = fixture({ "lib/i18n/dictionary.ts": TERNARY });

    expect(run(["--root", root, "--strict"]).code).toBe(0);
  });
});
