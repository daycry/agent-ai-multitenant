#!/usr/bin/env node
/**
 * Guarda de tamaño de pantalla (plan prod-16, `task_prod16_08`).
 *
 * El hallazgo frontend-10 medía diez `page.tsx` por encima de 800 líneas. El
 * problema de fondo no es el número: es que **crecen solos**. Cada feature
 * añade un diálogo, un filtro o una sección al mismo fichero porque es donde ya
 * está el estado, y nadie mide hasta que la pantalla es inmanejable — de los
 * diez del plan, cuatro ya no son los mismos: unos se partieron y otros
 * (`teams/[team_id]`, `cortex/mind`, `projects/[id]/chat`) cruzaron el límite
 * DESPUÉS de escribirse el plan. Sin trinquete, partir hoy sólo compra tiempo.
 *
 * Son DOS medidas con la misma mecánica, cada una con su techo y su allowlist:
 *
 *   1. **Pantallas** (`page.tsx`), techo 800 — lo que pide el plan.
 *   2. **Piezas** del troceado (`*-section`, `*-dialog`, `*-tab`, `*-panel`,
 *      `*-table`), techo 500 (`SECTION_MAX_LINES`). Sin esta segunda medida la
 *      guarda **premia el atajo**: mudar 700 líneas del `page.tsx` a un solo
 *      `algo-sections.tsx` bajaba el contador sin haber partido nada, y eso pasó
 *      —`mcp-server-sections.tsx` acabó en 1125 líneas dando OK.
 *
 * Mecánica idéntica a `check-i18n.mjs`, a propósito (una sola forma de guarda
 * que aprender):
 *
 * - Un `page.tsx` fuera de la allowlist por encima del límite ⇒ error. Es el
 *   caso importante: pantalla NUEVA que nace obesa.
 * - Un fichero de la allowlist con MÁS líneas que las anotadas ⇒ error.
 * - Con MENOS ⇒ aviso: has partido, baja el número (no falla, para no bloquear
 *   una mejora, pero se ve en la salida).
 * - Por debajo del límite ⇒ aviso para borrarlo de la allowlist.
 * - `--strict`: cualquier fichero por encima del límite es error, esté o no en
 *   la allowlist. Es el modo del día que la deuda quede saldada.
 *
 * La guarda se autocomprueba: si el recorrido deja de encontrar ficheros, falla
 * en vez de pasar en vacío (verificar-antes-de-implementar §4).
 *
 * Uso:
 *   node scripts/check-component-size.mjs
 *   node scripts/check-component-size.mjs --max-lines 800
 *   node scripts/check-component-size.mjs --strict
 *   node scripts/check-component-size.mjs --root <dir>        # para los tests
 *   node scripts/check-component-size.mjs --allowlist <json>  # ídem, ver main()
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Carpetas que se recorren. */
const SCAN_DIRS = ["app", "components", "lib"];

/** No se recorren nunca. */
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "test-results", "vendor"]);

/** Límite por defecto, el que fija `task_prod16_08`. */
const DEFAULT_MAX_LINES = 800;

/**
 * Deuda conocida de PANTALLAS, fichero → líneas. **Este mapa sólo puede
 * MENGUAR**: cada partición debe bajar el número o borrar la línea.
 *
 * **Vacío desde el 2026-08-10**, y eso es el hito de `task_prod16_08`: ninguna
 * de las 81 pantallas del panel pasa de 800 líneas. Las últimas dos en caer
 * fueron `cortex/mind` (914 → 327) y `projects/[id]/chat` (926 → 462). Con el
 * mapa vacío, `check-component-size.mjs` y `--strict` dicen lo mismo para las
 * pantallas: la deuda está saldada y el trinquete pasa a ser sólo prevención.
 *
 * Los que el plan nombraba y ya se partieron (`model-prices`, `mcp-servers`,
 * `plans/[planId]`, `knowledge-bases`, `tenant-stats`, `llm-providers`,
 * `agents/[id]`, `notifications`, `settings/sso`, `settings/sso/saml`,
 * `teams/[team_id]`) tampoco están aquí, por lo mismo.
 */
const ALLOWLIST = {};

/** Techo de una pieza del troceado. Lo fija `task_prod16_06`. */
const SECTION_MAX_LINES = 500;

/**
 * Deuda de PIEZAS. **Sólo puede MENGUAR.**
 *
 * Siguen siendo dos entradas, pero ya no son el mismo caso que el 2026-08-01:
 *
 * · `mcp-server-sections.tsx` (1125) **ya no existe**. Era el ejemplar del
 *   problema —el tramo de modularización #9 sacó el bulto del `page.tsx` sin
 *   repartirlo— y el 2026-08-10 se repartió de verdad en cuatro piezas:
 *   `mcp-server-card` (~85), `mcp-test-result-panel` (~130),
 *   `mcp-tool-roles-section` (~235) y `mcp-server-dialog`, que hereda el resto.
 *   Los 15 tests del módulo siguen verdes sin tocar una aserción.
 *
 * · `mcp-server-dialog.tsx` queda por encima del techo **a propósito, y está
 *   argumentado en su docstring**: es UN formulario con una decena de `useState`
 *   entrelazados, y partirlo pide decidir cómo viaja ese estado (prop-drilling o
 *   contexto local). Eso no es un movimiento mecánico, es un rediseño con riesgo
 *   de regresión. Anotarlo aquí con su tamaño real es deuda medida y decreciente;
 *   partirlo a lo bruto para que el número baje sería el atajo que esta guarda
 *   existe para castigar.
 *
 * · `agent-tools-section.tsx` (691) sigue igual: el mismo caso del tramo #9,
 *   pendiente de una pasada propia.
 */
const SECTION_ALLOWLIST = {
  "app/admin/agents/[id]/agent-tools-section.tsx": 691,
  "app/admin/projects/[id]/mcp-servers/mcp-server-dialog.tsx": 665,
};

/**
 * Qué cuenta como "pantalla": sólo `page.tsx`, que es lo que pide el plan y lo
 * que ve el usuario.
 */
function isScreen(rel) {
  return rel.endsWith("/page.tsx") || rel === "page.tsx";
}

/**
 * Qué cuenta como "pieza": el DESTINO del troceado.
 *
 * Durante un tiempo estas quedaron fuera a propósito, con el argumento de que
 * medirlas con la misma vara penalizaría el movimiento que la guarda quiere
 * premiar. El argumento era malo y el propio script lo admitía en una nota: "su
 * tamaño se vigila a ojo en review — que `mcp-server-sections.tsx` acabara en
 * 1125 líneas es la prueba de que mover el bulto no es partir". Vigilar a ojo es
 * no vigilar, y una guarda que sólo mira `page.tsx` **premia el atajo**: sacar
 * 700 líneas a un `algo-sections.tsx` baja el contador sin haber partido nada.
 *
 * La vara no es la misma: las piezas tienen su propio techo, más bajo
 * (`SECTION_MAX_LINES`), que es el que el plan fija en `task_prod16_06`
 * ("`page.tsx` < 400 líneas, ninguna sección > 500"). Partir sigue premiado;
 * lo que deja de estarlo es mudar el monolito de fichero.
 */
const SECTION_SUFFIXES = [
  "-section.tsx",
  "-sections.tsx",
  "-dialog.tsx",
  "-dialogs.tsx",
  "-tab.tsx",
  "-tabs.tsx",
  "-panel.tsx",
  "-table.tsx",
];

function isSection(rel) {
  return SECTION_SUFFIXES.some((suffix) => rel.endsWith(suffix));
}

/** Mínimo de ficheros que el recorrido DEBE ver para creerse a sí mismo. */
const MIN_FILES_SCANNED = 100;

function parseArgs(argv) {
  const args = {
    root: APP_ROOT,
    strict: false,
    maxLines: DEFAULT_MAX_LINES,
    allowlistPath: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--strict") args.strict = true;
    else if (argv[i] === "--allowlist") {
      args.allowlistPath = resolve(argv[i + 1] ?? ".");
      i += 1;
    } else if (argv[i] === "--root") {
      args.root = resolve(argv[i + 1] ?? ".");
      i += 1;
    } else if (argv[i] === "--max-lines") {
      args.maxLines = Number(argv[i + 1] ?? DEFAULT_MAX_LINES);
      i += 1;
    }
  }
  return args;
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

/** Líneas de un fichero, contadas como `wc -l` + 1 si no acaba en salto. */
function countLines(abs) {
  const source = readFileSync(abs, "utf8");
  if (source === "") return 0;
  const n = source.split("\n").length;
  return source.endsWith("\n") ? n - 1 : n;
}

function main() {
  const { root, strict, maxLines, allowlistPath } = parseArgs(process.argv.slice(2));

  // Allowlists EFECTIVAS. Por defecto las reales; `--allowlist <json>` las
  // sustituye por unas de fixture y su único consumidor es
  // `check-component-size.test.ts`.
  //
  // Existe porque la mecánica del trinquete —"la deuda no puede crecer", "con
  // --strict no se perdona a nadie"— no puede depender de que HAYA deuda. Antes
  // el test leía la allowlist real: primero clavando un nombre a mano, luego con
  // `--print-allowlist`. Las dos versiones se pusieron rojas **por el éxito**, la
  // segunda el día que la allowlist de pantallas se vació (2026-08-10). Un test
  // que sólo puede probar el trinquete mientras exista deuda deja de probarlo
  // justo cuando el trinquete es lo único que la mantiene en cero.
  const screenAllowlist = { ...ALLOWLIST };
  const sectionAllowlist = { ...SECTION_ALLOWLIST };
  if (allowlistPath) {
    const injected = JSON.parse(readFileSync(allowlistPath, "utf8"));
    for (const key of Object.keys(screenAllowlist)) delete screenAllowlist[key];
    for (const key of Object.keys(sectionAllowlist)) delete sectionAllowlist[key];
    Object.assign(screenAllowlist, injected.screens ?? {});
    Object.assign(sectionAllowlist, injected.sections ?? {});
  }

  const files = collectFiles(root);
  const isFixture = root !== APP_ROOT;

  const sizes = new Map();
  const sectionSizes = new Map();
  for (const rel of files) {
    if (/\.test\.tsx?$/.test(rel)) continue;
    if (isScreen(rel)) sizes.set(rel, countLines(join(root, rel)));
    else if (isSection(rel)) sectionSizes.set(rel, countLines(join(root, rel)));
  }

  const errors = [];
  const notes = [];
  let over = 0;
  let sectionsOver = 0;

  for (const [rel, count] of sizes) {
    if (count <= maxLines) {
      if (!strict && screenAllowlist[rel] !== undefined) {
        notes.push(
          `${rel}: ${count} líneas, ya por debajo de ${maxLines} — bórralo de la allowlist.`,
        );
      }
      continue;
    }

    over += 1;
    const allowed = strict ? 0 : (screenAllowlist[rel] ?? 0);
    if (allowed === 0) {
      errors.push(
        `${rel}: ${count} líneas (límite ${maxLines}). Trocéalo en secciones colocadas ` +
          "(patrón de app/admin/agents/[id]/*-section.tsx).",
      );
    } else if (count > allowed) {
      errors.push(
        `${rel}: ${count} líneas, la allowlist anota ${allowed}. La deuda no puede crecer.`,
      );
    } else if (count < allowed) {
      notes.push(`${rel}: ${count} < ${allowed} anotadas — baja el número en la allowlist.`);
    }
  }

  // Las piezas del troceado, con su propio techo. Sin esto la guarda premia el
  // atajo: mudar el monolito a un `*-sections.tsx` baja el contador de pantallas
  // sin haber partido nada.
  for (const [rel, count] of sectionSizes) {
    if (count <= SECTION_MAX_LINES) {
      if (!strict && sectionAllowlist[rel] !== undefined) {
        notes.push(
          `${rel}: ${count} líneas, ya por debajo de ${SECTION_MAX_LINES} — ` +
            "bórralo de SECTION_ALLOWLIST.",
        );
      }
      continue;
    }

    sectionsOver += 1;
    const allowed = strict ? 0 : (sectionAllowlist[rel] ?? 0);
    if (allowed === 0) {
      errors.push(
        `${rel}: ${count} líneas (techo de pieza ${SECTION_MAX_LINES}). Repártela: ` +
          "sacar el bulto del page.tsx a un solo fichero no es partirlo.",
      );
    } else if (count > allowed) {
      errors.push(
        `${rel}: ${count} líneas, SECTION_ALLOWLIST anota ${allowed}. La deuda no puede crecer.`,
      );
    } else if (count < allowed) {
      notes.push(`${rel}: ${count} < ${allowed} anotadas — baja el número en SECTION_ALLOWLIST.`);
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
    if (sizes.size === 0) {
      errors.push(
        "no se encontró NINGÚN page.tsx: el descubrimiento de pantallas dejó de " +
          "funcionar (¿cambió la convención de rutas de Next?).",
      );
    }
    if (sectionSizes.size === 0) {
      errors.push(
        "no se encontró NINGUNA pieza (*-section/-dialog/-tab/-panel/-table): " +
          "SECTION_SUFFIXES dejó de casar con cómo se nombran, y el techo de las " +
          "piezas estaría pasando en vacío.",
      );
    }
  }

  console.log(
    `check-component-size: ${files.length} ficheros, ${sizes.size} pantalla(s), ` +
      `${over} por encima de ${maxLines} línea(s); ${sectionSizes.size} pieza(s), ` +
      `${sectionsOver} pieza(s) por encima de ${SECTION_MAX_LINES}` +
      `${strict ? " [strict]" : ""}`,
  );
  for (const note of notes) console.log(`  aviso: ${note}`);

  if (errors.length > 0) {
    console.error("\ncheck-component-size FALLA:");
    for (const error of errors) console.error(`  - ${error}`);
    process.exit(1);
  }

  console.log("check-component-size OK");
}

main();
