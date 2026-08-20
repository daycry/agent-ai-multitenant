#!/usr/bin/env node
/**
 * Guarda de regresión i18n (plan prod-16, `task_prod16_01`).
 *
 * El panel traducía a mano, con ternarios `lang === "es" ? … : …` repartidos por
 * el código (hallazgo frontend-9). La fundación de `lib/i18n/` los sustituye,
 * pero la migración de los ~100 ficheros restantes son `task_prod16_02` a
 * `task_prod16_04`: mientras dure, hace falta un trinquete que impida que la
 * deuda CREZCA.
 *
 * Son DOS trinquetes con la misma mecánica, cada uno con su allowlist:
 *
 *   1. **Ternarios** `lang === "es" ? …` — traducir a mano (`ALLOWLIST`).
 *   2. **Atributos de UI con castellano cableado** — el grueso real de la deuda,
 *      añadido en `task_prod16_03` (`ATTR_ALLOWLIST`, ver `ATTR_PATTERN`).
 *
 * Reglas (idénticas para los dos):
 *
 * - Un fichero que no esté en su allowlist y tenga infractores ⇒ error. Es el
 *   caso importante: código NUEVO que vuelve a escribir castellano fijo.
 * - Un fichero de la allowlist con MÁS de los anotados ⇒ error.
 * - Con MENOS ⇒ aviso: has migrado, baja el número (no falla, para no bloquear
 *   una mejora, pero se ve en la salida).
 * - `--strict` (lo usa `task_prod16_04` al cerrar la migración): cualquier
 *   infractor, esté o no en la allowlist, es error.
 *
 * La propia guarda se autocomprueba: si el descubrimiento deja de encontrar
 * ficheros o deja de encontrar los infractores conocidos, falla en vez de pasar
 * en vacío — el modo de fallo del §4 de docs/03-guides/verificar-antes-de-implementar.md.
 *
 * Uso:
 *   node scripts/check-i18n.mjs
 *   node scripts/check-i18n.mjs --strict
 *   node scripts/check-i18n.mjs --root <dir>     # para los tests
 *   node scripts/check-i18n.mjs --print-allowlist  # sólo para los tests, ver main()
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Carpetas que se recorren. */
const SCAN_DIRS = ["app", "components", "lib"];

/** No se recorren nunca. */
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "test-results", "vendor"]);

/**
 * `lib/i18n/` es la única casa legítima de la palabra: ahí vive el diccionario
 * y su documentación.
 */
const EXEMPT_PREFIXES = ["lib/i18n/"];

/**
 * **VACÍA desde el 2026-08-12: el trinquete de ternarios está GRADUADO.**
 *
 * El plan prod-16 nació con 63 ternarios de idioma en 12 ficheros; el 08-01
 * quedaban 34 en 8, el 08-10 nueve en 4, y este lote cerró los cuatro últimos
 * (`app/admin/tools`, `agent-tools-diagnostic`, el respaldo del banner de
 * honestidad del córtex y `components/teams/adopt-team-dialog`). Con el mapa
 * vacío el umbral es cero para todo el mundo, así que **el modo normal y
 * `--strict` dicen ya lo mismo para los ternarios**: la guarda deja de saldar
 * deuda y pasa a impedirla. Es el mismo final que tuvo la mitad de «pantallas»
 * de `check-component-size.mjs`.
 *
 * **Volver a añadir una entrada aquí es reabrir la deuda**, y hay un test que
 * afirma que el mapa está vacío (`el trinquete de ternarios está GRADUADO`):
 * si alguien la añade, se entera.
 *
 * El ternario `lang === "es" ? … : …` tapaba DOS cosas distintas, y las dos
 * tienen su sustituto en `lib/i18n/`:
 *
 *   1. **Texto de UI escrito a mano** — se conoce al compilar, así que va al
 *      diccionario: `translate(lang, "ns", "clave")` desde un módulo puro, o
 *      `useT("ns")` desde un componente. Así lo cubren los invariantes de
 *      `i18n.test.ts` (las dos caras presentes, no vacías, sin copia-pega).
 *   2. **Texto bilingüe que llega en DATOS** — una nota `note_es`/`note_en` del
 *      córtex, el label de un runtime template, un aviso del backend. No hay
 *      clave posible: se resuelve con `pickLang(lang, { es, en })`, que además
 *      cae al otro idioma si el pedido viene vacío.
 *
 * Y una advertencia que este mapa se llevó al cerrarse: **contaba UNO por
 * fichero cuando el atajo estaba bien escrito**. Dos ficheros de `capability`
 * escondían veinte textos detrás de un `const t = (es, en) => …` local, y el
 * mapa marcaba 1. Cero ternarios NO significa cero deuda de i18n: significa que
 * ya no queda la forma de deuda que esta señal sabe ver.
 */
const ALLOWLIST = {};

const PATTERN = /lang === "es"/g;

/**
 * Muestra de control del detector de ternarios.
 *
 * Con la allowlist vacía se perdió la autocomprobación que decía «no encuentro
 * ningún ternario y debería»: a cero, no encontrar nada es lo correcto. Sin algo
 * que la sustituya, `PATTERN` podría pudrirse (un cambio de comillas, un
 * refactor del literal) y la guarda pasaría en vacío para siempre — el modo de
 * fallo del §4 de docs/03-guides/verificar-antes-de-implementar.md, que es justo
 * el que estas guardas existen para no repetir.
 *
 * Este literal se busca a sí mismo: si deja de casar exactamente una vez, la
 * guarda falla en vez de dar OK. Vive en `scripts/`, que NO está en `SCAN_DIRS`,
 * así que no se cuenta como deuda.
 */
const PATTERN_PROBE = 'const x = lang === "es" ? a : b;';

/**
 * Segundo trinquete: castellano CABLEADO en un atributo que ve el usuario.
 *
 * El de los ternarios sólo cubre los ficheros que ya traducían a mano, que eran
 * 18. El grueso de la deuda de frontend-9 no son ternarios: son literales fijos
 * que con el toggle en EN se quedan en castellano y no se queja nadie.
 *
 * ## La medida mentía, y por eso hay dos señales
 *
 * La primera versión sólo miraba caracteres que existen SÓLO en castellano
 * (tilde, ñ, ¿, ¡). Exacto y con cero falsos positivos… y ciego a media deuda:
 * `title="Dar acceso a un proyecto"` no lleva una sola tilde, así que el guard
 * daba `exit 0` sobre un fichero sin traducir. **Medía la deuda detectable, no
 * la deuda**, y su número tranquilizaba más de lo que debía — que en un
 * trinquete es el peor defecto posible, porque el número es justo lo que se
 * mira para decidir si queda trabajo.
 *
 * Ahora son TRES señales, cualquiera basta:
 *
 *   1. **Un carácter exclusivo del castellano** (tilde, ñ, ¿, ¡).
 *   2. **Una palabra de la lista** — incluidas las de contenido, porque el
 *      grueso de los botones del panel es de una sola palabra (`title="Guardar"`).
 *   3. **Un sufijo que no existe como final de palabra inglesa** (`-ciones`,
 *      `-idad`, `-miento`, `-mente`…). Cubre los cognados largos sin tener que
 *      enumerarlos: `Notificaciones`, `Seguridad`, `Almacenamiento`.
 *
 * Y DOS filtros, que son lo que impide que la guarda se vuelva insoportable —
 * porque un guard con falsos positivos se desactiva a la tercera y entonces no
 * mide nada:
 *
 *   * **Identificadores, slugs y URLs no son prosa.** `equipo-plataforma` lleva
 *     «equipo» dentro y es un slug de ejemplo; `vault:secret/data/mcp/…` lleva
 *     «servicio». Un valor sin espacios y con `-`, `_`, `.`, `:`, `/` o dígitos
 *     es un identificador y no se juzga.
 *   * **Las siglas en mayúsculas no son palabras castellanas.** «SE», «UN»,
 *     «SIN»/«CON» de un enum: en castellano esas palabras van en minúscula.
 *     Distinguir por caja cuesta una línea y borra una familia entera de falsos
 *     positivos.
 *
 * Sólo atributos que el usuario LEE: `className`, `data-testid` o `href` con una
 * ñ no son un problema de traducción.
 */
const UI_ATTRS = [
  "placeholder",
  "aria-label",
  "title",
  "loadingLabel",
  "emptyLabel",
  "description",
  "label",
];
const SPANISH_CHARS = "áéíóúüñÁÉÍÓÚÜÑ¿¡";

/**
 * Palabras que en un atributo de UI sólo pueden ser castellano.
 *
 * Criterio de admisión: la palabra NO es también inglesa y NO es un nombre
 * propio frecuente. De ahí que falten `no`, `son`, `sin`, `con`, `todo`, `plan`,
 * `local`, `data` y `error`, que colisionan; y `es`, que además es el código de
 * idioma y aparece en identificadores.
 */
const SPANISH_WORDS = [
  // artículos, preposiciones y conjunciones
  "el",
  "la",
  "los",
  "las",
  "un",
  "una",
  "unos",
  "unas",
  "del",
  "que",
  "para",
  "por",
  "como",
  "cuando",
  "donde",
  "desde",
  "hasta",
  "sobre",
  "entre",
  "cada",
  "pero",
  "porque",
  "este",
  "esta",
  "estos",
  "estas",
  "ese",
  "esa",
  "sus",
  "hay",
  // verbos de UI — sueltos, porque el grueso de los botones es de una palabra
  "crear",
  "editar",
  "borrar",
  "eliminar",
  "guardar",
  "buscar",
  "seleccionar",
  "elegir",
  "quitar",
  "activar",
  "desactivar",
  "cancelar",
  "confirmar",
  "actualizar",
  "cargar",
  "mostrar",
  "ocultar",
  "enviar",
  "volver",
  "aplicar",
  "cerrar",
  "abrir",
  "agregar",
  "copiar",
  "descargar",
  "subir",
  "exportar",
  "importar",
  "reintentar",
  "detener",
  "empezar",
  "continuar",
  "asignar",
  // sustantivos de UI
  "nombre",
  "usuario",
  "usuarios",
  "proyecto",
  "proyectos",
  "equipo",
  "equipos",
  "tarea",
  "tareas",
  "fecha",
  "ajustes",
  "ninguno",
  "ninguna",
  "todos",
  "todas",
  "nuevo",
  "nueva",
  "acceso",
  "clave",
  "correo",
  "aviso",
  "campo",
  "agente",
  "agentes",
  "estado",
  "cambios",
  "pendiente",
  "pendientes",
  "disponible",
  "disponibles",
  "requerido",
  "obligatorio",
  "vacio",
  "vacia",
];
const SPANISH_WORD_SET = new Set(SPANISH_WORDS);

/**
 * Finales que no existen como final de palabra INGLESA.
 *
 * Ahorran enumerar los cognados largos, que son justo los que más se cuelan
 * («Notificaciones», «Almacenamiento», «Seguridad»). Se exige una longitud
 * mínima para que un `-ado` de «Colorado» o un `-ez` de un apellido no cuenten.
 */
const SPANISH_SUFFIXES = [
  "cion",
  "ciones",
  "idad",
  "idades",
  "miento",
  "mientos",
  "mente",
  "ando",
  "iendo",
  "anza",
  "encia",
  "encias",
  "aje",
  "ura",
];
const MIN_SUFFIX_WORD_LENGTH = 6;

/** Un valor sin espacios y con puntuación de identificador o dígitos no es prosa. */
const IDENTIFIER_RE = /^[^\s]*[-_.:/@\d][^\s]*$/;

/** Los atributos de UI con su valor, para poder juzgar el valor y no la línea. */
const ATTR_VALUE_PATTERN = new RegExp(`(?:${UI_ATTRS.join("|")})="([^"]*)"`, "g");

/** ¿El valor de este atributo está en castellano? Ver el bloque de arriba. */
export function looksSpanish(value) {
  const text = value.trim();
  if (!text) return false;
  // Un identificador, un slug o una URL no son prosa que traducir.
  if (IDENTIFIER_RE.test(text)) return false;
  if (new RegExp(`[${SPANISH_CHARS}]`).test(text)) return true;

  for (const raw of text.split(/[^A-Za-zÀ-ÿ]+/)) {
    if (!raw) continue;
    // Sigla: en castellano estas palabras van en minúscula, así que un token
    // todo-mayúsculas es un acrónimo («UN SDK», «SIN / CON» de un enum).
    if (raw.length > 1 && raw === raw.toUpperCase()) continue;
    const word = raw.toLowerCase();
    if (SPANISH_WORD_SET.has(word)) return true;
    if (word.length >= MIN_SUFFIX_WORD_LENGTH) {
      for (const suffix of SPANISH_SUFFIXES) {
        if (word.endsWith(suffix)) return true;
      }
    }
  }
  return false;
}

/**
 * Deuda de atributos conocida el 2026-08-01. **Sólo puede MENGUAR.**
 *
 * Los ficheros migrados (login, shell, sidebar, select-tenant, no-access, users,
 * backup, tenant-stats, llm-providers, model-prices, agents, knowledge-bases)
 * NO están aquí: están a cero y el trinquete los mantiene así.
 *
 * `knowledge-bases` es el aviso de para qué NO sirve este mapa. Tenía **3
 * entradas** —una por fichero— y eso lo hacía parecer un lote de diez minutos.
 * Detrás había ~2.100 líneas de castellano cableado en cinco ficheros: el
 * patrón sólo ve atributos con tilde, así que un módulo entero escrito sin
 * acentos en los `<Button>` sale a 0 estando sin traducir. El contador mide la
 * deuda que se puede detectar sola, no la deuda.
 *
 * **2026-08-19** — salen seis entradas más, y con ellas 12 atributos (200 → 188,
 * 77 → 71 ficheros). Dos lotes:
 *
 *   * `settings/page.tsx`, `settings/memories/page.tsx` y
 *     `settings/platform-defaults/page.tsx`. Llevaban semanas aquí no por falta
 *     de ganas sino porque el BACKEND sólo servía `label_es`/`description_es`:
 *     sin el par `_en` en el registry, traducir el marco dejaba la pantalla
 *     mitad-y-mitad. El par existe desde hoy y `require_language_pair` lo valida
 *     al importar el módulo, así que el bloqueo se levantó y las tres se
 *     migraron enteras (más `cortex-model-section.tsx`, que se renderiza dentro
 *     de la tercera).
 *   * `projects/page.tsx`, `projects/[id]/memories/page.tsx` y
 *     `projects/[id]/dep-cache/page.tsx`, tres pantallas autocontenidas del
 *     módulo de proyectos.
 *
 * **2026-08-20** — salen las SEIS entradas de `settings/sso/` (16 atributos:
 * 188 → 172, 71 → 65 ficheros), y con ellas el módulo `settings/` queda entero
 * salvo lo que nunca tuvo deuda. Entra COMPLETO —las dos pantallas, OIDC y
 * SAML, con sus tarjetas, sus fichas y sus DOS diálogos— porque el mapa sólo
 * veía 16 atributos de un módulo de ~2.000 líneas: los dos diálogos, que son
 * más de la mitad del texto, sumaban 8 de esos 16 y contenían otras 60 cadenas
 * que ninguna de las tres señales sabe ver. Migrar lo marcado habría dejado al
 * operador rellenando en castellano el formulario que decide quién entra al
 * tenant.
 *
 * Y una que NO cuenta el mapa porque nunca tuvo entrada: `ProjectBreadcrumb`
 * escribía "Proyectos" fijo, así que la miga de pan de las DIEZ sub-pantallas
 * del proyecto seguía en castellano con el toggle en EN. El literal no está en
 * un atributo —es una propiedad de un objeto— y por eso ninguna de las dos
 * señales lo veía. Tercer ejemplo del mismo aviso: el contador mide su patrón,
 * no la deuda.
 *
 * **2026-08-19, cuarta pasada** — salen QUINCE entradas más (35 atributos) al
 * migrar enteros `memories`, `assistant`, `notifications`, `docs` y las tres
 * pantallas que le faltaban a `marketplace`, más los cuatro comboboxes
 * compartidos.
 *
 * Y con ellas, la cuarta demostración del mismo aviso: `docs` son DOCE ficheros
 * y ~2.300 líneas, y este mapa sólo le veía **ocho atributos en cinco ficheros**
 * porque su deuda vivía en texto JSX suelto —los seis estados de cada panel—,
 * no en atributos. `assistant` era peor todavía: sus ocho etiquetas de
 * herramienta y sus cinco mensajes de validación estaban en `lib/assistant.ts`,
 * un módulo PURO, donde ni este mapa ni el de ternarios miran.
 */
const ATTR_ALLOWLIST = {
  "app/admin/agents/[id]/agent-kbs-section.tsx": 1,
  "app/admin/approval-policy/page.tsx": 2,
  "app/admin/approvals/page.tsx": 3,
  "app/admin/board/page.tsx": 4,
  // El córtex baja de 20 a 9 al cerrarse las dos casillas de UI (F2 «MindPanel»
  // y F3.6 «IdentityCard»): el Panel de Mente (`mind/page.tsx` 3 +
  // `mind/affect-panel.tsx` 1) y la identidad (`identity/page.tsx` 7) están a
  // CERO y salen del mapa, así que el trinquete los mantiene ahí. Las dos
  // casillas pedían «ES+EN» y seguían abiertas justamente por esto.
  // Queda el chat (`cortex/page.tsx`), que es de la fase F1 y no de estas dos.
  "app/admin/cortex/page.tsx": 9,
  "app/admin/dashboard/page.tsx": 3,
  "app/admin/documents/[id]/citations/page.tsx": 2,
  "app/admin/documents/[id]/ingestion/page.tsx": 2,
  "app/admin/documents/page.tsx": 2,
  "app/admin/eval-quality/page.tsx": 4,
  "app/admin/executions/[id]/page.tsx": 5,
  "app/admin/human-agents/page.tsx": 3,
  "app/admin/inbox/history-tab.tsx": 3,
  "app/admin/inbox/page.tsx": 2,
  "app/admin/inbox/submit-dialog.tsx": 3,
  "app/admin/office/page.tsx": 6,
  "app/admin/plans/[id]/escalated/page.tsx": 2,
  // El troceo de la pantalla (prod-16 `task_prod16_08`) repartió esta deuda
  // entre el `page.tsx` y la pieza que se llevó el `placeholder` del textarea.
  // 5 = 4 + 1: no crece, cambia de fichero.
  // El troceo de prod-16 `task_prod16_08` repartió `mcp-server-sections.tsx`
  // (1125 líneas) en cuatro piezas. La deuda de atributos se reparte con él:
  // 3 = 2 + 1, mismo total, dos ficheros.
  "app/admin/projects/[id]/mcp-servers/mcp-server-card.tsx": 2,
  "app/admin/projects/[id]/mcp-servers/mcp-server-dialog.tsx": 1,
  "app/admin/projects/[id]/mcp-servers/page.tsx": 2,
  "app/admin/projects/[id]/page.tsx": 1,
  "app/admin/projects/[id]/plans/[planId]/page.tsx": 1,
  "app/admin/projects/[id]/plans/[planId]/plan-spec-sections.tsx": 2,
  "app/admin/projects/[id]/plans/[planId]/plan-validation-section.tsx": 1,
  "app/admin/projects/[id]/plans/page.tsx": 4,
  "app/admin/projects/[id]/tasks/page.tsx": 4,
  "app/developers/api-reference/page.tsx": 3,
  "app/developers/sdks/page.tsx": 1,
  "app/developers/tutorials/page.tsx": 3,
  "app/developers/webhooks/page.tsx": 2,
  "components/cortex/cortex-voice-call.tsx": 2,
  "components/evals/launch-eval-run.tsx": 1,
  "components/executions/execution-guidance.tsx": 1,
  "components/executions/replay-bar.tsx": 2,
  "components/layout/tenant-picker.tsx": 1,
  "components/projects/git-config-section.tsx": 2,
  "components/projects/governance-section.tsx": 2,
  "components/projects/runtime-services-section.tsx": 6,
  "components/shared/form-section.tsx": 4,
  "components/shared/list-toolbar.tsx": 1,
  "components/tasks/task-detail-sheet.tsx": 3,
  "components/tasks/task-human-actions.tsx": 7,
  "components/ui/markdown-textarea.tsx": 2,
  "lib/plan-dag.tsx": 1,
  "lib/plan-gantt.tsx": 1,
};

/** Mínimo de ficheros que el recorrido DEBE ver para creerse a sí mismo. */
const MIN_FILES_SCANNED = 50;

function parseArgs(argv) {
  const args = { root: APP_ROOT, strict: false, printAllowlist: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--strict") args.strict = true;
    else if (argv[i] === "--print-allowlist") args.printAllowlist = true;
    else if (argv[i] === "--print-current") args.printCurrent = true;
    else if (argv[i] === "--root") {
      args.root = resolve(argv[i + 1] ?? ".");
      i += 1;
    }
  }
  return args;
}

/**
 * La deuda medida: `[ternarios, atributos]`, cada uno `Map<fichero, nº>`.
 *
 * Una sola función para los dos consumidores —la comprobación y
 * `--print-current`— porque si midieran distinto, re-basar el trinquete lo
 * dejaría desalineado con lo que comprueba, que es peor que no re-basarlo.
 */
function measure(files, root) {
  const counts = new Map();
  const attrCounts = new Map();
  for (const rel of files) {
    if (EXEMPT_PREFIXES.some((prefix) => rel.startsWith(prefix))) continue;
    const source = readFileSync(join(root, rel), "utf8");

    const hits = (source.match(PATTERN) ?? []).length;
    if (hits > 0) counts.set(rel, hits);

    // Los tests llevan castellano en sus fixtures a propósito: no es UI.
    if (/\.test\.tsx?$/.test(rel)) continue;
    // Se juzga el VALOR del atributo, no la línea: `looksSpanish` necesita ver
    // el texto suelto para contar palabras sin que el nombre del atributo ni el
    // JSX de alrededor le sumen coincidencias.
    let attrHits = 0;
    for (const match of source.matchAll(ATTR_VALUE_PATTERN)) {
      if (looksSpanish(match[1])) attrHits += 1;
    }
    if (attrHits > 0) attrCounts.set(rel, attrHits);
  }
  return [counts, attrCounts];
}

/** Rutas relativas (con `/`) de todos los .ts/.tsx bajo `root`. */
function collectFiles(root) {
  const found = [];

  const walk = (relDir) => {
    const absDir = join(root, relDir);
    let entries;
    try {
      entries = readdirSync(absDir);
    } catch {
      return; // carpeta ausente: en un fixture es normal
    }
    for (const entry of entries) {
      const rel = relDir ? `${relDir}/${entry}` : entry;
      if (statSync(join(root, rel)).isDirectory()) {
        if (!SKIP_DIRS.has(entry)) walk(rel);
      } else if (entry.endsWith(".ts") || entry.endsWith(".tsx")) {
        found.push(rel);
      }
    }
  };

  for (const dir of SCAN_DIRS) walk(dir);
  return found.sort();
}

function main() {
  const { root, strict, printAllowlist, printCurrent } = parseArgs(process.argv.slice(2));

  // Su ÚNICO consumidor es `check-i18n.test.ts`. Sus fixtures necesitan un
  // fichero que las allowlists conozcan, y clavar el nombre a mano convierte
  // cada migración exitosa en cuatro tests rojos: le pasó al guard hermano el
  // 2026-08-01, cuando `llm-providers` se partió de verdad. Leerlo de aquí hace
  // que el test siga a la deuda en vez de a un nombre.
  if (printAllowlist) {
    process.stdout.write(JSON.stringify({ ternaries: ALLOWLIST, attrs: ATTR_ALLOWLIST }));
    return;
  }

  // `--print-current` emite la deuda REAL medida ahora, no la anotada. Es lo que
  // hace reproducible re-basar el trinquete el día que la medida se afina y
  // aparece deuda que estaba oculta: sin él, la única salida es editar 90
  // entradas a mano, que es como se acaba aflojando una allowlist por cansancio.
  // NO relaja nada: la allowlist sigue siendo la que manda al comprobar.
  if (printCurrent) {
    const [ternaries, attrs] = measure(collectFiles(root), root);
    process.stdout.write(
      JSON.stringify({
        ternaries: Object.fromEntries([...ternaries].sort()),
        attrs: Object.fromEntries([...attrs].sort()),
      }),
    );
    return;
  }

  const files = collectFiles(root);
  const isFixture = root !== APP_ROOT;

  const [counts, attrCounts] = measure(files, root);

  const errors = [];
  const notes = [];

  for (const [rel, hits] of counts) {
    const allowed = strict ? 0 : (ALLOWLIST[rel] ?? 0);
    if (hits > allowed) {
      errors.push(
        allowed === 0
          ? `${rel}: ${hits} ternario(s) 'lang === "es"'. Usa useT() de @/lib/i18n.`
          : `${rel}: ${hits} ternario(s), la allowlist permite ${allowed}. La deuda no puede crecer.`,
      );
    } else if (hits < allowed) {
      notes.push(`${rel}: ${hits} < ${allowed} permitidos — baja el número en la allowlist.`);
    }
  }

  for (const [rel, hits] of attrCounts) {
    const allowed = strict ? 0 : (ATTR_ALLOWLIST[rel] ?? 0);
    if (hits > allowed) {
      errors.push(
        allowed === 0
          ? `${rel}: ${hits} atributo(s) de UI con texto castellano fijo. Usa useT() de @/lib/i18n.`
          : `${rel}: ${hits} atributo(s) en castellano, la allowlist permite ${allowed}. La deuda no puede crecer.`,
      );
    } else if (hits < allowed) {
      notes.push(
        `${rel}: ${hits} < ${allowed} atributos permitidos — baja el número en la allowlist.`,
      );
    }
  }

  if (!strict) {
    for (const rel of Object.keys(ALLOWLIST)) {
      if (!counts.has(rel) && !isFixture) {
        notes.push(`${rel}: ya no tiene ternarios — bórralo de la allowlist.`);
      }
    }
    for (const rel of Object.keys(ATTR_ALLOWLIST)) {
      if (!attrCounts.has(rel) && !isFixture) {
        notes.push(`${rel}: ya no tiene atributos en castellano — bórralo de la allowlist.`);
      }
    }
  }

  // --- autocomprobaciones: una guarda que no puede fallar no es una guarda ---
  if (!isFixture) {
    if (files.length < MIN_FILES_SCANNED) {
      errors.push(
        `el recorrido sólo vio ${files.length} ficheros (< ${MIN_FILES_SCANNED}): ` +
          "SCAN_DIRS o el cwd están mal, la guarda estaría pasando en vacío.",
      );
    }
    // Con la allowlist ya vacía (trinquete graduado), no encontrar ternarios es
    // el resultado CORRECTO, no una señal de que el patrón se rompió. Lo que
    // sustituye a aquella comprobación es la muestra de control.
    if (!strict && counts.size === 0 && Object.keys(ALLOWLIST).length > 0) {
      errors.push(
        "no se encontró NINGÚN ternario y la allowlist no está vacía: el patrón " +
          "de búsqueda dejó de funcionar (o la migración terminó y toca --strict).",
      );
    }
    if ((PATTERN_PROBE.match(PATTERN) ?? []).length !== 1) {
      errors.push(
        "el detector de ternarios no reconoce su propia muestra de control: " +
          "PATTERN dejó de funcionar y la guarda estaría pasando en vacío.",
      );
    }
    if (!strict && attrCounts.size === 0 && Object.keys(ATTR_ALLOWLIST).length > 0) {
      errors.push(
        "no se encontró NINGÚN atributo en castellano y su allowlist no está vacía: " +
          "ATTR_PATTERN dejó de funcionar (o la migración terminó y toca --strict).",
      );
    }
  }

  const pending = [...counts.values()].reduce((a, b) => a + b, 0);
  const attrPending = [...attrCounts.values()].reduce((a, b) => a + b, 0);
  console.log(
    `check-i18n: ${files.length} ficheros, ${pending} ternario(s) pendientes en ${counts.size} fichero(s), ` +
      `${attrPending} atributo(s) pendientes en ${attrCounts.size} fichero(s)` +
      `${strict ? " [strict]" : ""}`,
  );
  for (const note of notes) console.log(`  aviso: ${note}`);

  if (errors.length > 0) {
    console.error("\ncheck-i18n FALLA:");
    for (const error of errors) console.error(`  - ${error}`);
    console.error(
      "\nTraduce con el diccionario (`useT(" +
        '"namespace"' +
        ")` de @/lib/i18n) en vez de con ternarios.",
    );
    process.exit(1);
  }

  console.log("check-i18n OK");
}

// Sólo se ejecuta al INVOCARLO, no al importarlo. Sin esta guarda, un test que
// quisiera probar `looksSpanish` a solas disparaba el barrido entero y su
// `process.exit(1)`: el export existía y era inusable.
const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) main();
