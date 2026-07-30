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
 * Reglas:
 *
 * - Un fichero que no esté en `ALLOWLIST` y tenga ternarios ⇒ error. Es el caso
 *   importante: código NUEVO que vuelve a traducir a mano.
 * - Un fichero de la allowlist con MÁS ternarios que los anotados ⇒ error.
 * - Con MENOS ⇒ aviso: has migrado, baja el número (no falla, para no bloquear
 *   una mejora, pero se ve en la salida).
 * - `--strict` (lo usa `task_prod16_04` al cerrar la migración): cualquier
 *   ternario, esté o no en la allowlist, es error.
 *
 * La propia guarda se autocomprueba: si el descubrimiento deja de encontrar
 * ficheros o deja de encontrar los infractores conocidos, falla en vez de pasar
 * en vacío — el modo de fallo del §4 de docs/03-guides/verificar-antes-de-implementar.md.
 *
 * Uso:
 *   node scripts/check-i18n.mjs
 *   node scripts/check-i18n.mjs --strict
 *   node scripts/check-i18n.mjs --root <dir>     # para los tests
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
 * Deuda conocida el 2026-07-30, fichero → nº de ternarios. **Este mapa sólo
 * puede MENGUAR.** Cada lote de migración de prod-16 debe borrar líneas de aquí.
 */
const ALLOWLIST = {
  "app/admin/agents/[id]/agent-tools-section.tsx": 6,
  "app/admin/agents/[id]/page.tsx": 1,
  "app/admin/agents/page.tsx": 3,
  "app/admin/cortex/mind/page.tsx": 1,
  "app/admin/projects/[id]/agent-tools-diagnostic/page.tsx": 3,
  "app/admin/tools/page.tsx": 4,
  "components/capability/capability-hub.tsx": 8,
  "components/capability/chat-model-section.tsx": 1,
  "components/capability/persona-section.tsx": 15,
  "components/capability/provider-model-selects.tsx": 1,
  "components/teams/adopt-team-dialog.tsx": 1,
  "lib/capability/hub.ts": 13,
  "lib/cortex-curiosity.ts": 4,
  "lib/cortex-identity.ts": 5,
  "lib/memory/honesty.ts": 3,
  "lib/persona/persona.ts": 5,
  "lib/runtime-templates.ts": 1,
  "lib/tools/taxonomy.ts": 2,
};

const PATTERN = /lang === "es"/g;

/** Mínimo de ficheros que el recorrido DEBE ver para creerse a sí mismo. */
const MIN_FILES_SCANNED = 50;

function parseArgs(argv) {
  const args = { root: APP_ROOT, strict: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--strict") args.strict = true;
    else if (argv[i] === "--root") {
      args.root = resolve(argv[i + 1] ?? ".");
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

function main() {
  const { root, strict } = parseArgs(process.argv.slice(2));
  const files = collectFiles(root);
  const isFixture = root !== APP_ROOT;

  const counts = new Map();
  for (const rel of files) {
    if (EXEMPT_PREFIXES.some((prefix) => rel.startsWith(prefix))) continue;
    const hits = (readFileSync(join(root, rel), "utf8").match(PATTERN) ?? []).length;
    if (hits > 0) counts.set(rel, hits);
  }

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

  if (!strict) {
    for (const rel of Object.keys(ALLOWLIST)) {
      if (!counts.has(rel) && !isFixture) {
        notes.push(`${rel}: ya no tiene ternarios — bórralo de la allowlist.`);
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
    if (!strict && counts.size === 0) {
      errors.push(
        "no se encontró NINGÚN ternario y la allowlist no está vacía: el patrón " +
          "de búsqueda dejó de funcionar (o la migración terminó y toca --strict).",
      );
    }
  }

  const pending = [...counts.values()].reduce((a, b) => a + b, 0);
  console.log(
    `check-i18n: ${files.length} ficheros, ${pending} ternario(s) pendientes en ${counts.size} fichero(s)` +
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

main();
