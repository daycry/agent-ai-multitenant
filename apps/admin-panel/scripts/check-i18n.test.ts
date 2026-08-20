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

/**
 * Cuarto bloque: **texto JSX suelto** (2026-08-20).
 *
 * Los dos detectores anteriores miran ATRIBUTOS y TERNARIOS. La deuda que se
 * encontró a mano siete veces esta semana no vivía en ninguno de los dos: vivía
 * en el texto que hay ENTRE etiquetas, que es justamente el que más lee el
 * usuario. `components/login/mfa-challenge.tsx` estaba entero en castellano
 * cableado —la ayuda, el botón «Verificar», los tres errores— y las dos guardas
 * decían cero.
 *
 * Lo que hace fiable a esta señal es la POSICIÓN, no la lista de palabras: el
 * texto entre dos etiquetas se renderiza por construcción, así que juzgar su
 * idioma siempre significa algo. Es la diferencia con un literal de cadena
 * cualquiera, que puede ser un valor de enum o la mitad de un par bilingüe — y
 * por eso el detector amplio se descartó (ver el bloque siguiente).
 */
describe("check-i18n — texto JSX suelto entre etiquetas", () => {
  it("el caso real de esta semana: MfaChallenge, entero en castellano suelto", () => {
    const root = fixture({
      "components/login/mfa-challenge.tsx": [
        "export function MfaChallenge() {",
        "  return (",
        "    <div>",
        "      <p>Introduce el código de verificación de tu aplicación</p>",
        "      <Button>Verificar</Button>",
        "    </div>",
        "  );",
        "}",
      ].join("\n"),
    });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(1);
    expect(output).toContain("components/login/mfa-challenge.tsx");
    expect(output).toContain("texto(s) JSX");
  });

  it("una pantalla YA migrada que reintroduce texto suelto salta (es el trinquete)", () => {
    // `users` está migrada y fuera de las tres allowlists. Este es el caso que
    // da valor a la guarda: proteger el trabajo que se acaba de hacer.
    const root = fixture({
      "app/admin/users/page.tsx": "<p>No se pudo cargar la lista de usuarios.</p>\n",
    });

    expect(run(["--root", root]).code).toBe(1);
  });

  it("cuenta ocurrencias, no ficheros", () => {
    const root = fixture({
      "app/x.tsx": "<p>Cargando ejecución…</p>\n<span>Sin hilos todavía</span>\n",
    });

    expect(run(["--root", root]).output).toContain("2 texto(s) JSX");
  });

  it("texto JSX en inglés no cuenta", () => {
    const root = fixture({
      "app/x.tsx": "<p>Could not load the user list.</p>\n<Button>Save</Button>\n",
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("lib/i18n/ sigue exento y los .ts puros no se miran (no hay JSX en ellos)", () => {
    const root = fixture({
      "lib/i18n/dictionary.ts": "<p>Cargando ejecución…</p>\n",
      "lib/api.ts": "const q = a > b && c < d;\n",
    });

    expect(run(["--root", root]).code).toBe(0);
  });
});

/**
 * Y los falsos positivos que el detector de texto JSX tiene que NO dar.
 *
 * Un guard con falsos positivos se desactiva a la tercera y entonces no mide
 * nada: le pasó a la guarda de tests declarados, que perdió autoridad con
 * cuatro. Estos casos son la mitad del arreglo que impide que esto acabe en una
 * allowlist de excusas.
 */
describe("check-i18n — el texto JSX no da falsos positivos", () => {
  it("los COMENTARIOS de este repo están en castellano y no son UI", () => {
    // El riesgo nº1: todo el código está comentado en castellano, y un
    // comentario que mencione etiquetas casaría con el patrón. Si esto saltara,
    // la guarda sería inservible en este repositorio.
    const root = fixture({
      "app/x.tsx": [
        "// Pasa de <Input> a <Field> cuando la selección está vacía.",
        "/* El <Button> lleva su propio título de sección. */",
        "/**",
        " * Envuelve <Foo> en <Bar> para que la validación herede el año.",
        " */",
        "export const x = 1;",
      ].join("\n"),
    });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("0 texto(s) JSX");
  });

  it("comparaciones y genéricos de TypeScript no son texto JSX", () => {
    const root = fixture({
      "app/x.tsx": [
        "const visible = total > minimo && contador < maximo;",
        "const m: Record<string, Ejecucion> | Map<string, Categoria> = load();",
        "const f = (a: Fila) => a.numeroDeAgentes < 5;",
        "export { visible, m, f };",
      ].join("\n"),
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("una expresión entre llaves no es texto suelto (es un valor, no copy)", () => {
    const root = fixture({
      "app/x.tsx": '<p>{estadoDeLaEjecucion}</p>\n<span>{t("plan.cargando")}</span>\n',
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("NO castiga el par bilingüe `{es, en}`, que es la solución y no la deuda", () => {
    // `lib/cortex-curiosity.ts` es exactamente esto y está BIEN: el texto llega
    // en datos y lo resuelve `pickLang`. Un detector de literales de cadena a
    // secas lo marcaba como deuda — castigar la solución es peor que no medir.
    const root = fixture({
      "lib/cortex-curiosity.ts": [
        "const LABELS = {",
        '  searching: { es: "investigando", en: "researching" },',
        '  failed: { es: "falló", en: "failed" },',
        "};",
        "export default LABELS;",
      ].join("\n"),
    });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("un tipo unión con valores internos en castellano no es copy de UI", () => {
    // `CapabilityLevel = "rol" | "stack" | "plataforma" | "equipo"` son valores
    // del dominio, no texto que se traduzca.
    const root = fixture({
      "lib/capability/hub.ts": 'export type Nivel = "rol" | "plataforma" | "equipo";\n',
    });

    expect(run(["--root", root]).code).toBe(0);
  });
});

/**
 * Y la asimetría que tenía el detector de TERNARIOS, encontrada al añadir el de
 * texto JSX (2026-08-20).
 *
 * El de atributos salta los ficheros de test y ninguno de los dos quitaba
 * comentarios. Consecuencia: **documentar el patrón contaba como cometerlo**. Se
 * vio en vivo — un `components/shared/i18n.test.tsx` recién escrito por el carril
 * de migración decía en su cabecera «no hay ni un `lang === "es"`» y la guarda lo
 * marcó como deuda. Es el falso positivo más caro que puede tener un trinquete:
 * castiga a quien lo está saldando, y con eso se gana que lo desactiven.
 */
describe("check-i18n — mencionar el patrón no es usarlo", () => {
  it("un ternario dentro de un COMENTARIO no es deuda", () => {
    const root = fixture({
      "components/x.tsx": [
        "/**",
        ' * Esta pantalla ya no usa `lang === "es"`: pasó al diccionario.',
        " */",
        'export const x = 1; // ni un lang === "es" por aquí',
      ].join("\n"),
    });

    const { code, output } = run(["--root", root]);

    expect(code).toBe(0);
    expect(output).toContain("0 ternario(s)");
  });

  it("los ficheros de test se saltan también para ternarios (como para atributos)", () => {
    // Un test de i18n compara las dos caras y puede nombrar el patrón en su
    // prosa o en un fixture. No es UI: la asimetría con los atributos era un
    // descuido, no una decisión.
    const root = fixture({ "components/shared/i18n.test.tsx": TERNARY });

    expect(run(["--root", root]).code).toBe(0);
  });

  it("pero un ternario de VERDAD en código sigue siendo deuda", () => {
    // La otra mitad: que quitar comentarios no haya abierto un agujero.
    const root = fixture({
      "components/x.tsx": ["// Comentario inocente.", TERNARY.trim()].join("\n"),
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
