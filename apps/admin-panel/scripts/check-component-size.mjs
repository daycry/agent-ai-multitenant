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
 * Los cuatro que el plan nombraba y ya se partieron (`model-prices`,
 * `mcp-servers`, `plans/[planId]`, `knowledge-bases`) NO están aquí: están por
 * debajo del límite y el trinquete los mantiene así.
 */
const ALLOWLIST = {
  "app/admin/agents/[id]/page.tsx": 824,
  "app/admin/cortex/mind/page.tsx": 914,
  "app/admin/llm-providers/page.tsx": 996,
  "app/admin/notifications/page.tsx": 831,
  "app/admin/projects/[id]/chat/page.tsx": 926,
  "app/admin/settings/sso/page.tsx": 915,
  "app/admin/settings/sso/saml/page.tsx": 943,
  "app/admin/teams/[team_id]/page.tsx": 914,
};

/**
 * Qué cuenta como "pantalla".
 *
 * Sólo `page.tsx`, que es lo que pide el plan y lo que ve el usuario. Las
 * secciones colocadas (`*-section.tsx`, `*-sections.tsx`) quedan fuera a
 * propósito: son el DESTINO del troceado, y medirlas con la misma vara
 * penalizaría justo el movimiento que la guarda quiere premiar. Su tamaño se
 * vigila a ojo en review — que `mcp-server-sections.tsx` acabara en 1125 líneas
 * es la prueba de que mover el bulto no es partir.
 */
function isScreen(rel) {
  return rel.endsWith("/page.tsx") || rel === "page.tsx";
}

/** Mínimo de ficheros que el recorrido DEBE ver para creerse a sí mismo. */
const MIN_FILES_SCANNED = 100;

function parseArgs(argv) {
  const args = { root: APP_ROOT, strict: false, maxLines: DEFAULT_MAX_LINES };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--strict") args.strict = true;
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
  const { root, strict, maxLines } = parseArgs(process.argv.slice(2));
  const files = collectFiles(root);
  const isFixture = root !== APP_ROOT;

  const sizes = new Map();
  for (const rel of files) {
    if (!isScreen(rel)) continue;
    if (/\.test\.tsx?$/.test(rel)) continue;
    sizes.set(rel, countLines(join(root, rel)));
  }

  const errors = [];
  const notes = [];
  let over = 0;

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
  }

  console.log(
    `check-component-size: ${files.length} ficheros, ${sizes.size} pantalla(s), ` +
      `${over} por encima de ${maxLines} línea(s)${strict ? " [strict]" : ""}`,
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
