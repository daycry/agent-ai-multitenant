/**
 * Personal assistant — shared types + pure logic (Plan 10 task assistant-ui).
 *
 * The assistant is Tenant-Admin-only and toggle-gated; the BACKEND enforces
 * that (403). This module only carries:
 *   - the request/response shapes of the three `/assistant/*` endpoints
 *     (the contract — no endpoints invented here),
 *   - the friendly, ES-labelled catalogue of the read tools the admin can
 *     enable for the assistant (mirrors `assistant/tools.py` /
 *     `DEFAULT_ENABLED_TOOLS` server-side),
 *   - pure validation for the identity form, factored out so a vitest can
 *     exercise it without rendering React.
 *
 * Kept framework-free on purpose: no React, no fetch — just data + functions.
 */

// ---------------------------------------------------------------------------
// Endpoint contract (the three endpoints in routers/assistant.py)
// ---------------------------------------------------------------------------

/** Languages the backend accepts (SUPPORTED_LANGUAGES in assistant/config.py). */
export const ASSISTANT_LANGUAGES = ["es", "en"] as const;
export type AssistantLanguage = (typeof ASSISTANT_LANGUAGES)[number];

/** GET/PUT /assistant/identity payload shape. */
export interface AssistantIdentity {
  name: string;
  avatar_url: string | null;
  tone: string;
  language: string;
  system_prompt_override: string | null;
  enabled_tools: string[];
}

/** PUT /assistant/identity request body. */
export interface AssistantIdentityUpdate {
  name: string;
  avatar_url: string | null;
  tone: string;
  language: AssistantLanguage;
  system_prompt_override: string | null;
  enabled_tools: string[];
}

/** POST /assistant/chat request / response. */
export interface AssistantChatRequest {
  message: string;
}
export interface AssistantChatResponse {
  answer: string;
  tools_called: string[];
  rounds: number;
}

// ---------------------------------------------------------------------------
// Field limits — mirror the Pydantic schema (schemas/assistant.py) so the
// client rejects the same shapes the server would 422 on.
// ---------------------------------------------------------------------------
export const ASSISTANT_LIMITS = {
  name: { min: 1, max: 120 },
  avatarUrl: { max: 2048 },
  tone: { min: 1, max: 200 },
  systemPrompt: { max: 8000 },
  message: { min: 1, max: 4000 },
} as const;

// ---------------------------------------------------------------------------
// Friendly tool catalogue (UX priority: tool assignment must be intuitive).
//
// Names + order MIRROR DEFAULT_ENABLED_TOOLS in assistant/config.py. The
// server intersects the submitted list with its catalogue, so an unknown
// name can never widen the surface; this list is purely the friendly
// presentation. Each entry gets an ES label + one-line description.
// ---------------------------------------------------------------------------
export interface AssistantToolDef {
  /** Canonical tool name sent to the backend (must match the server catalogue). */
  name: string;
  /** Friendly ES label for the checkbox. */
  label: string;
  /** One-line ES description of what the tool lets the assistant read. */
  description: string;
}

export const ASSISTANT_TOOL_CATALOGUE: readonly AssistantToolDef[] = [
  {
    name: "tenant_projects_status",
    label: "Estado de proyectos",
    description: "Conteo y estado consolidado de todos los proyectos del tenant.",
  },
  {
    name: "tenant_plans_summary",
    label: "Resumen de planes",
    description:
      "Planes cross-proyecto agrupados por estado, incluyendo los pendientes de aprobación.",
  },
  {
    name: "tenant_recent_activity",
    label: "Actividad reciente",
    description: "Tareas no terminales más recientes y total de tareas abiertas del tenant.",
  },
  {
    name: "tenant_budget_status",
    label: "Estado de presupuesto",
    description: "Gasto del periodo actual frente al presupuesto del tenant y sus proyectos.",
  },
  {
    name: "tenant_human_workload",
    label: "Carga de agentes humanos",
    description: "Tareas humanas activas y sesiones de trabajo de un usuario esta semana.",
  },
  {
    name: "tenant_human_assignments_pending",
    label: "Asignaciones humanas pendientes",
    description: "Tareas humanas sin aceptar desde hace más de N horas (por defecto 24h).",
  },
] as const;

/** Friendly label for a tool name, falling back to the raw name. */
export function assistantToolLabel(name: string): string {
  return ASSISTANT_TOOL_CATALOGUE.find((t) => t.name === name)?.label ?? name;
}

// ---------------------------------------------------------------------------
// Pure form validation (exercised by a vitest)
// ---------------------------------------------------------------------------
export interface AssistantIdentityFormValues {
  name: string;
  avatarUrl: string;
  tone: string;
  language: string;
  systemPrompt: string;
  enabledTools: string[];
}

export interface AssistantIdentityFormErrors {
  name?: string;
  avatarUrl?: string;
  tone?: string;
  language?: string;
  systemPrompt?: string;
}

/** True when `value` is a language the backend accepts. */
export function isSupportedLanguage(value: string): value is AssistantLanguage {
  return (ASSISTANT_LANGUAGES as readonly string[]).includes(value);
}

/**
 * Validate the identity form against the same bounds the Pydantic schema
 * enforces. Returns a per-field error map (empty = valid). Trims strings
 * the way the backend does (`str_strip_whitespace=True`) before measuring.
 */
export function validateAssistantIdentity(
  values: AssistantIdentityFormValues,
): AssistantIdentityFormErrors {
  const errors: AssistantIdentityFormErrors = {};

  const name = values.name.trim();
  if (name.length < ASSISTANT_LIMITS.name.min) {
    errors.name = "El nombre es obligatorio.";
  } else if (name.length > ASSISTANT_LIMITS.name.max) {
    errors.name = `El nombre no puede superar ${ASSISTANT_LIMITS.name.max} caracteres.`;
  }

  const tone = values.tone.trim();
  if (tone.length < ASSISTANT_LIMITS.tone.min) {
    errors.tone = "El tono es obligatorio.";
  } else if (tone.length > ASSISTANT_LIMITS.tone.max) {
    errors.tone = `El tono no puede superar ${ASSISTANT_LIMITS.tone.max} caracteres.`;
  }

  const avatar = values.avatarUrl.trim();
  if (avatar.length > ASSISTANT_LIMITS.avatarUrl.max) {
    errors.avatarUrl = `La URL no puede superar ${ASSISTANT_LIMITS.avatarUrl.max} caracteres.`;
  }

  const prompt = values.systemPrompt.trim();
  if (prompt.length > ASSISTANT_LIMITS.systemPrompt.max) {
    errors.systemPrompt = `El prompt no puede superar ${ASSISTANT_LIMITS.systemPrompt.max} caracteres.`;
  }

  if (!isSupportedLanguage(values.language)) {
    errors.language = "Idioma no soportado.";
  }

  return errors;
}

/** True when the form has no validation errors. */
export function isAssistantIdentityValid(values: AssistantIdentityFormValues): boolean {
  return Object.keys(validateAssistantIdentity(values)).length === 0;
}

/**
 * Build the PUT request body from form values, normalising the way the
 * backend does: trim strings, collapse empty optional strings to `null`,
 * keep only catalogue tool names (server intersects anyway, but we keep the
 * payload honest). Assumes the form already passed validation.
 */
export function toIdentityUpdate(values: AssistantIdentityFormValues): AssistantIdentityUpdate {
  const avatar = values.avatarUrl.trim();
  const prompt = values.systemPrompt.trim();
  const known = new Set(ASSISTANT_TOOL_CATALOGUE.map((t) => t.name));
  const language: AssistantLanguage = isSupportedLanguage(values.language) ? values.language : "es";
  return {
    name: values.name.trim(),
    avatar_url: avatar.length > 0 ? avatar : null,
    tone: values.tone.trim(),
    language,
    system_prompt_override: prompt.length > 0 ? prompt : null,
    enabled_tools: ASSISTANT_TOOL_CATALOGUE.map((t) => t.name).filter(
      (n) => known.has(n) && values.enabledTools.includes(n),
    ),
  };
}

/** Seed editable form values from a fetched identity. */
export function identityToFormValues(identity: AssistantIdentity): AssistantIdentityFormValues {
  return {
    name: identity.name,
    avatarUrl: identity.avatar_url ?? "",
    tone: identity.tone,
    language: isSupportedLanguage(identity.language) ? identity.language : "es",
    systemPrompt: identity.system_prompt_override ?? "",
    enabledTools: [...identity.enabled_tools],
  };
}
