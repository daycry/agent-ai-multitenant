/**
 * Fuente ÚNICA de la taxonomía visual de tools del admin-panel
 * (Plan 06.18 task_06_18_10 · ADR 0049 — tres facetas Función / Seguridad /
 * Origen).
 *
 * ¿Por qué este módulo existe?
 *   Antes, la asignación (`agent-tools-section.tsx`) y el diagnóstico
 *   (`agent-tools-diagnostic/page.tsx`) mantenían SUS PROPIOS mapas de
 *   labels/variants y divergían: el diagnóstico usaba una clave `sensitive`
 *   que NO existe en el enum del backend (`ToolSecurityLevel` = safe /
 *   sandboxed / privileged), le faltaba `sandboxed`, y `docker_command` salía
 *   en `danger` en un sitio e `info` en otro. Resultado: la MISMA tool se veía
 *   distinta según la pantalla y, cuando una clave fallaba, se renderizaba el
 *   enum crudo en inglés (`docker_command`, `privileged`…).
 *
 *   Aquí centralizamos las tres facetas. Cualquier pantalla que muestre una
 *   tool importa de aquí, así una tool dada muestra SIEMPRE el mismo
 *   label/variant en cualquier parte de la app.
 *
 * Reglas:
 *   - Los value-sets canónicos espejan los enums del backend
 *     (`api_server.db.domain`): NUNCA inventar claves. La clave inexistente
 *     `sensitive` se eliminó a propósito.
 *   - Cada faceta ofrece `labelEs` + `labelEn` (CLAUDE.md §12: ES + EN), una
 *     `variant` del primitivo <Badge> y un `help` en lenguaje llano para el
 *     tooltip. NUNCA se debe renderizar el enum crudo: usa `resolve*` que,
 *     ante un valor desconocido, devuelve un descriptor humanizado en vez del
 *     slug.
 */

import type { BadgeVariant } from "@/components/ui/badge";
import type { Lang } from "@/lib/lang-context";

// ---------------------------------------------------------------------------
// Closed value-sets (mirror api_server.db.domain enums — ADR 0049)
// ---------------------------------------------------------------------------

/** `ToolSecurityLevel` — facet *Seguridad*. */
export type SecurityLevel = "safe" | "sandboxed" | "privileged";

/** `ToolImplementationType` — facet *Origen* (cómo se implementa/ejecuta). */
export type ImplementationType =
  | "builtin"
  | "python_function"
  | "http_endpoint"
  | "mcp_tool"
  | "docker_command";

/** `ToolCategory` — facet *Función*. */
export type ToolCategory =
  | "file"
  | "runtime"
  | "git"
  | "network"
  | "knowledge"
  | "notification"
  | "command"
  | "mcp"
  | "orchestration"
  | "custom";

/**
 * One taxonomy term, ready to render: bilingual label, semantic <Badge>
 * variant and a plain-language help string for the accessible tooltip.
 */
export interface TaxonomyDescriptor {
  /** Canonical slug this descriptor resolves (the backend enum value). */
  value: string;
  labelEs: string;
  labelEn: string;
  variant: BadgeVariant;
  /** Plain-language help, in the active language. */
  help: string;
}

interface BilingualTerm {
  labelEs: string;
  labelEn: string;
  variant: BadgeVariant;
  helpEs: string;
  helpEn: string;
}

// ---------------------------------------------------------------------------
// Seguridad (ToolSecurityLevel) — safe / sandboxed / privileged
// Note: NO `sensitive` key. It never existed in the backend enum; the old
// diagnostic invented it. `sandboxed` → "Aislada".
// ---------------------------------------------------------------------------
export const SECURITY: Record<SecurityLevel, BilingualTerm> = {
  safe: {
    labelEs: "Segura",
    labelEn: "Safe",
    variant: "success",
    helpEs: "Solo lectura / sin efectos secundarios — sin riesgo.",
    helpEn: "Read-only / no side effects — no risk.",
  },
  sandboxed: {
    labelEs: "Aislada",
    labelEn: "Sandboxed",
    variant: "warning",
    helpEs: "Modifica dentro del sandbox de la tarea (worktree/contenedor efímero).",
    helpEn: "Mutates inside the task sandbox (worktree / ephemeral container).",
  },
  privileged: {
    labelEs: "Privilegiada",
    labelEn: "Privileged",
    variant: "danger",
    helpEs: "Capacidad potente (p. ej. ejecutar comandos): asígnala con criterio.",
    helpEn: "Powerful capability (e.g. running commands): assign with care.",
  },
};

// ---------------------------------------------------------------------------
// Origen / Implementación (ToolImplementationType)
// `docker_command` unified to a single variant ("info") across the app — it
// used to diverge danger vs info between assignment and diagnostic.
// ---------------------------------------------------------------------------
export const IMPL: Record<ImplementationType, BilingualTerm> = {
  builtin: {
    labelEs: "Nativa",
    labelEn: "Built-in",
    variant: "muted",
    helpEs: "Implementada de forma nativa por la plataforma.",
    helpEn: "Implemented natively by the platform.",
  },
  mcp_tool: {
    labelEs: "MCP",
    labelEn: "MCP",
    variant: "success",
    helpEs: "Proporcionada por un servidor MCP configurado en el proyecto.",
    helpEn: "Provided by an MCP server configured on the project.",
  },
  http_endpoint: {
    labelEs: "HTTP",
    labelEn: "HTTP",
    variant: "info",
    helpEs: "Llama a un endpoint HTTP externo.",
    helpEn: "Calls an external HTTP endpoint.",
  },
  python_function: {
    labelEs: "Python",
    labelEn: "Python",
    variant: "warning",
    helpEs: "Ejecuta una función Python registrada.",
    helpEn: "Runs a registered Python function.",
  },
  docker_command: {
    labelEs: "Contenedor",
    labelEn: "Container",
    variant: "info",
    helpEs: "Se ejecuta dentro de un contenedor efímero aislado.",
    helpEn: "Runs inside an isolated ephemeral container.",
  },
};

// ---------------------------------------------------------------------------
// Función (ToolCategory)
// ---------------------------------------------------------------------------
export const CATEGORY: Record<ToolCategory, BilingualTerm> = {
  file: {
    labelEs: "Archivos",
    labelEn: "Files",
    variant: "muted",
    helpEs: "Lectura/escritura de ficheros del workspace.",
    helpEn: "Read/write workspace files.",
  },
  runtime: {
    labelEs: "Ejecución / Tests",
    labelEn: "Runtime / Tests",
    variant: "muted",
    helpEs: "Ejecuta tests o procesos del runtime.",
    helpEn: "Runs tests or runtime processes.",
  },
  git: {
    labelEs: "Git",
    labelEn: "Git",
    variant: "muted",
    helpEs: "Operaciones de control de versiones.",
    helpEn: "Version-control operations.",
  },
  network: {
    labelEs: "Red",
    labelEn: "Network",
    variant: "muted",
    helpEs: "Acceso de red saliente.",
    helpEn: "Outbound network access.",
  },
  knowledge: {
    labelEs: "Conocimiento",
    labelEn: "Knowledge",
    variant: "muted",
    helpEs: "Búsqueda y recuperación de conocimiento (RAG).",
    helpEn: "Knowledge search and retrieval (RAG).",
  },
  notification: {
    labelEs: "Notificaciones",
    labelEn: "Notifications",
    variant: "muted",
    helpEs: "Envío de notificaciones.",
    helpEn: "Sends notifications.",
  },
  command: {
    labelEs: "Comandos shell",
    labelEn: "Shell commands",
    variant: "muted",
    helpEs: "Ejecuta comandos de shell autorizados.",
    helpEn: "Runs authorised shell commands.",
  },
  mcp: {
    labelEs: "MCP",
    labelEn: "MCP",
    variant: "muted",
    helpEs: "Tools importadas de un servidor MCP.",
    helpEn: "Tools imported from an MCP server.",
  },
  orchestration: {
    labelEs: "Orquestación",
    labelEn: "Orchestration",
    variant: "muted",
    helpEs: "Tools de orquestación registradas por el runtime.",
    helpEn: "Orchestration tools registered by the runtime.",
  },
  custom: {
    labelEs: "Personalizada",
    labelEn: "Custom",
    variant: "muted",
    helpEs: "Tool del tenant sin función estándar.",
    helpEn: "Tenant tool without a standard function bucket.",
  },
};

// ---------------------------------------------------------------------------
// Resolution helpers — NEVER render the raw enum
// ---------------------------------------------------------------------------

/**
 * Humanise an unknown slug instead of leaking the raw enum: `foo_bar` →
 * `Foo bar`. Used as the fallback for any value that is not in a closed set,
 * so the UI never shows `docker_command`, `privileged`, etc. verbatim.
 */
export function humanizeSlug(slug: string): string {
  const spaced = slug.replace(/_/g, " ").trim();
  if (spaced === "") return slug;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function resolve(term: BilingualTerm | undefined, value: string, lang: Lang): TaxonomyDescriptor {
  if (term) {
    return {
      value,
      labelEs: term.labelEs,
      labelEn: term.labelEn,
      variant: term.variant,
      help: lang === "es" ? term.helpEs : term.helpEn,
    };
  }
  // Unknown slug: humanise both labels (never the raw enum) and stay neutral.
  const human = humanizeSlug(value);
  return {
    value,
    labelEs: human,
    labelEn: human,
    variant: "muted",
    help: human,
  };
}

/** Resolve a security level to a render-ready descriptor in `lang`. */
export function resolveSecurity(value: string, lang: Lang): TaxonomyDescriptor {
  return resolve(SECURITY[value as SecurityLevel], value, lang);
}

/** Resolve an implementation/origin type to a render-ready descriptor in `lang`. */
export function resolveImpl(value: string, lang: Lang): TaxonomyDescriptor {
  return resolve(IMPL[value as ImplementationType], value, lang);
}

/** Resolve a category/function to a render-ready descriptor in `lang`. */
export function resolveCategory(value: string, lang: Lang): TaxonomyDescriptor {
  return resolve(CATEGORY[value as ToolCategory], value, lang);
}

/** The bilingual label for a descriptor in the active language. */
export function label(descriptor: TaxonomyDescriptor, lang: Lang): string {
  return lang === "es" ? descriptor.labelEs : descriptor.labelEn;
}
