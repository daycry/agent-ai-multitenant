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

/**
 * Segundo trinquete: castellano CABLEADO en un atributo que ve el usuario.
 *
 * El de los ternarios sólo cubre los ficheros que ya traducían a mano, que eran
 * 18. El grueso de la deuda de frontend-9 no son ternarios: son literales fijos
 * que con el toggle en EN se quedan en castellano y no se queja nadie.
 *
 * Detectar "esto está en castellano" en general es adivinar. Detectarlo en un
 * atributo con un carácter que SÓLO existe en castellano (tilde, ñ, ¿, ¡) es
 * exacto: cero falsos positivos, a cambio de no ver los literales sin acentuar.
 * Es un suelo, no un techo — el barrido de `task_prod16_04` sigue necesitando
 * ojo humano (test `human_prod16_01`).
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
const ATTR_PATTERN = new RegExp(`(?:${UI_ATTRS.join("|")})="[^"]*[${SPANISH_CHARS}][^"]*"`, "g");

/**
 * Deuda de atributos conocida el 2026-07-30. **Sólo puede MENGUAR.**
 *
 * Los ficheros migrados (login, shell, sidebar, select-tenant, no-access, users)
 * NO están aquí: están a cero y el trinquete los mantiene así.
 */
const ATTR_ALLOWLIST = {
  "app/admin/agents/[id]/agent-skills-section.tsx": 2,
  "app/admin/agents/[id]/agent-tools-section.tsx": 3,
  "app/admin/agents/page.tsx": 1,
  "app/admin/approval-policy/page.tsx": 2,
  "app/admin/approvals/page.tsx": 1,
  "app/admin/assistant/page.tsx": 2,
  "app/admin/assistant/settings/page.tsx": 1,
  "app/admin/backup/destinations/page.tsx": 1,
  "app/admin/backup/page.tsx": 2,
  "app/admin/backup/restore/page.tsx": 1,
  "app/admin/cortex/identity/page.tsx": 6,
  "app/admin/cortex/mind/page.tsx": 3,
  "app/admin/cortex/page.tsx": 7,
  "app/admin/docs/doc-diff-view.tsx": 2,
  "app/admin/docs/docs-search-panel.tsx": 1,
  "app/admin/docs/docs-sidebar.tsx": 1,
  "app/admin/docs/page.tsx": 2,
  "app/admin/documents/[id]/citations/page.tsx": 1,
  "app/admin/documents/[id]/ingestion/page.tsx": 1,
  "app/admin/eval-quality/page.tsx": 2,
  "app/admin/executions/[id]/page.tsx": 2,
  "app/admin/guardrails/page.tsx": 2,
  "app/admin/human-agents/page.tsx": 1,
  "app/admin/inbox/history-tab.tsx": 2,
  "app/admin/inbox/page.tsx": 2,
  "app/admin/inbox/submit-dialog.tsx": 1,
  "app/admin/knowledge-bases/categories/page.tsx": 1,
  "app/admin/knowledge-bases/kb-sections.tsx": 1,
  "app/admin/knowledge-bases/page.tsx": 1,
  "app/admin/llm-providers/page.tsx": 2,
  "app/admin/marketplace/installations/[id]/permissions/page.tsx": 1,
  "app/admin/marketplace/listings/[id]/playwright-config/page.tsx": 2,
  "app/admin/marketplace/page.tsx": 1,
  "app/admin/memories/page.tsx": 1,
  "app/admin/model-prices/model-price-dialogs.tsx": 1,
  "app/admin/model-prices/page.tsx": 3,
  "app/admin/notifications/inbox/page.tsx": 2,
  "app/admin/notifications/page.tsx": 1,
  "app/admin/office/page.tsx": 3,
  "app/admin/ollama/page.tsx": 1,
  "app/admin/plans/[id]/escalated/page.tsx": 1,
  "app/admin/projects/[id]/agent-tools-diagnostic/page.tsx": 2,
  "app/admin/projects/[id]/chat/page.tsx": 3,
  "app/admin/projects/[id]/commands/page.tsx": 1,
  "app/admin/projects/[id]/dep-cache/page.tsx": 1,
  "app/admin/projects/[id]/incoming-webhooks/page.tsx": 1,
  "app/admin/projects/[id]/knowledge-bases/page.tsx": 1,
  "app/admin/projects/[id]/mcp-servers/page.tsx": 1,
  "app/admin/projects/[id]/memories/page.tsx": 1,
  "app/admin/projects/[id]/plans/[planId]/plan-spec-sections.tsx": 2,
  "app/admin/projects/[id]/tasks/page.tsx": 1,
  "app/admin/settings/hourly-rate/page.tsx": 1,
  "app/admin/settings/memories/page.tsx": 1,
  "app/admin/settings/page.tsx": 1,
  "app/admin/settings/platform-defaults/page.tsx": 1,
  "app/admin/settings/sso/page.tsx": 2,
  "app/admin/settings/sso/saml/page.tsx": 4,
  "app/admin/tenant-stats/page.tsx": 4,
  "app/admin/tools/page.tsx": 7,
  "app/developers/api-reference/page.tsx": 3,
  "app/developers/sdks/page.tsx": 1,
  "app/developers/tutorials/page.tsx": 1,
  "app/developers/webhooks/page.tsx": 2,
  "components/cortex/cortex-voice-call.tsx": 1,
  "components/executions/replay-bar.tsx": 1,
  "components/projects/git-config-section.tsx": 2,
  "components/projects/governance-section.tsx": 2,
  "components/projects/runtime-services-section.tsx": 3,
  "components/shared/form-section.tsx": 2,
  "components/tasks/task-detail-sheet.tsx": 2,
  "components/tasks/task-human-actions.tsx": 5,
  "components/ui/entity-combobox.tsx": 1,
  "lib/plan-gantt.tsx": 1,
};

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
  const attrCounts = new Map();
  for (const rel of files) {
    if (EXEMPT_PREFIXES.some((prefix) => rel.startsWith(prefix))) continue;
    const source = readFileSync(join(root, rel), "utf8");

    const hits = (source.match(PATTERN) ?? []).length;
    if (hits > 0) counts.set(rel, hits);

    // Los tests llevan castellano en sus fixtures a propósito: no es UI.
    if (/\.test\.tsx?$/.test(rel)) continue;
    const attrHits = (source.match(ATTR_PATTERN) ?? []).length;
    if (attrHits > 0) attrCounts.set(rel, attrHits);
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
    if (!strict && counts.size === 0) {
      errors.push(
        "no se encontró NINGÚN ternario y la allowlist no está vacía: el patrón " +
          "de búsqueda dejó de funcionar (o la migración terminó y toca --strict).",
      );
    }
    if (!strict && attrCounts.size === 0) {
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

main();
