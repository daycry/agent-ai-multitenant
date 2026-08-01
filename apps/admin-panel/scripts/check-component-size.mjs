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
 *   node scripts/check-component-size.mjs --root <dir>   # para los tests
 *   node scripts/check-component-size.mjs --print-allowlist  # sólo para los tests, ver main()
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
 * Deuda conocida el 2026-08-01, fichero → líneas. **Este mapa sólo puede
 * MENGUAR**: cada partición debe bajar el número o borrar la línea.
 *
 * Los que el plan nombraba y ya se partieron (`model-prices`, `mcp-servers`,
 * `plans/[planId]`, `knowledge-bases`, `tenant-stats`, `llm-providers`,
 * `agents/[id]`, `notifications`) NO están aquí: están por debajo del límite y
 * el trinquete los mantiene así.
 */
const ALLOWLIST = {
  "app/admin/cortex/mind/page.tsx": 914,
  "app/admin/projects/[id]/chat/page.tsx": 926,
  "app/admin/settings/sso/page.tsx": 915,
  "app/admin/settings/sso/saml/page.tsx": 943,
  "app/admin/teams/[team_id]/page.tsx": 914,
};

/** Techo de una pieza del troceado. Lo fija `task_prod16_06`. */
const SECTION_MAX_LINES = 500;

/**
 * Deuda de PIEZAS conocida el 2026-08-01. **Sólo puede MENGUAR.**
 *
 * Son exactamente dos, y las dos son el mismo caso: el tramo de modularización
 * #9 sacó el bulto del `page.tsx` sin repartirlo. Todas las demás piezas del
 * panel están por debajo de 500 y el trinquete las mantiene ahí.
 */
const SECTION_ALLOWLIST = {
  "app/admin/agents/[id]/agent-tools-section.tsx": 691,
  "app/admin/projects/[id]/mcp-servers/mcp-server-sections.tsx": 1125,
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
    printAllowlist: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--strict") args.strict = true;
    else if (argv[i] === "--print-allowlist") args.printAllowlist = true;
    else if (argv[i] === "--root") {
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
  const { root, strict, maxLines, printAllowlist } = parseArgs(process.argv.slice(2));

  // Su ÚNICO consumidor es `check-component-size.test.ts`, y existe para que ese
  // test no clave un nombre de fichero de la allowlist en sus fixtures. Lo hacía,
  // y el día que `llm-providers` se partió de verdad —justo lo que la guarda
  // quiere premiar— cuatro tests suyos se pusieron rojos por el éxito. Un
  // acoplamiento que castiga el trabajo bien hecho hay que hacerlo explícito o
  // quitarlo; esto es quitarlo.
  if (printAllowlist) {
    process.stdout.write(JSON.stringify({ screens: ALLOWLIST, sections: SECTION_ALLOWLIST }));
    return;
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
      if (!strict && ALLOWLIST[rel] !== undefined) {
        notes.push(
          `${rel}: ${count} líneas, ya por debajo de ${maxLines} — bórralo de la allowlist.`,
        );
      }
      continue;
    }

    over += 1;
    const allowed = strict ? 0 : (ALLOWLIST[rel] ?? 0);
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
      if (!strict && SECTION_ALLOWLIST[rel] !== undefined) {
        notes.push(
          `${rel}: ${count} líneas, ya por debajo de ${SECTION_MAX_LINES} — ` +
            "bórralo de SECTION_ALLOWLIST.",
        );
      }
      continue;
    }

    sectionsOver += 1;
    const allowed = strict ? 0 : (SECTION_ALLOWLIST[rel] ?? 0);
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
