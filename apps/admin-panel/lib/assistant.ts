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
 * The only fetch helpers here are the assistant on/off toggle (Tenant-Admin
 * only, gated SOLELY by tenant_admin so it can actually be turned on). The
 * rest stays framework-free: pure data + functions.
 */

import { apiFetch } from "@/lib/api";

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
  // A1: hilo persistente — el backend crea/reutiliza y devuelve el id.
  conversation_id: string | null;
}

export interface AssistantConversationItem {
  id: string;
  title: string | null;
  updated_at: string;
}

export interface AssistantTurnItem {
  role: string;
  content: string;
  tools_called: string[];
  rounds: number;
  created_at: string;
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
  {
    name: "remember_about_me",
    label: "Recordar sobre ti",
    description:
      "Deja que el asistente guarde datos personales duraderos (tu nombre, preferencias, gustos) y los recuerde en futuras conversaciones.",
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

// ---------------------------------------------------------------------------
// Assistant on/off toggle (GET/PUT /tenant-settings/personal-assistant)
//
// This pair is the ONLY way a Tenant Admin enables the assistant for their
// tenant. The backend gates it SOLELY by `require_tenant_admin` (never by the
// assistant gate that requires the toggle ON), so it can actually be flipped
// on. The `/assistant/*` endpoints stay toggle-gated; this one does not.
// ---------------------------------------------------------------------------

/** GET/PUT /tenant-settings/personal-assistant payload shape. */
export interface AssistantToggleState {
  enabled: boolean;
}

/** Read the per-tenant assistant on/off toggle. Tenant-Admin-only (403 else). */
export function getAssistantEnabled(): Promise<AssistantToggleState> {
  return apiFetch<AssistantToggleState>("/tenant-settings/personal-assistant");
}

/** Flip the per-tenant assistant on/off toggle. Tenant-Admin-only (403 else). */
export function setAssistantEnabled(enabled: boolean): Promise<AssistantToggleState> {
  return apiFetch<AssistantToggleState>("/tenant-settings/personal-assistant", {
    method: "PUT",
    body: { enabled },
  });
}

// ---------------------------------------------------------------------------
// Model selection (ADR 0053) — what LLM provider/model the assistant uses.
//
// Inheritance: the tenant override (set here) wins over the platform default a
// System Admin configures. The BACKEND validates the selection against the
// closed catalogue (active provider + catalogued model) and enforces the
// gating; these are just the typed contract + thin fetch helpers.
// ---------------------------------------------------------------------------

/** GET /assistant/model — the effective model resolved for the tenant. */
export interface AssistantModel {
  provider_id: string | null;
  model_id: string | null;
  /** Which tier won, or null when nothing usable is configured. */
  source: "tenant_override" | "platform_default" | null;
  provider_kind: string | null;
  provider_display_name: string | null;
  has_tenant_override: boolean;
  /** ADR 0070: esfuerzo de razonamiento efectivo (null = sin razonar). */
  reasoning_effort: string | null;
}

/** One active provider + the model ids selectable on it (dropdown source). */
export interface AssistantModelOption {
  provider_id: string;
  kind: string;
  /** Unique kebab-case handle — disambiguates same-kind providers. */
  slug: string;
  display_name: string;
  models: string[];
}

/** GET /assistant/model/options (tenant) and /assistant/default-model/options. */
export interface AssistantModelOptions {
  providers: AssistantModelOption[];
  /** ADR 0070: opciones de razonamiento por kind de proveedor (off + niveles). */
  reasoning_by_kind?: Record<string, string[]>;
}

/** GET/PUT /assistant/default-model — the platform default (System Admin). */
export interface AssistantDefaultModel {
  provider_id: string | null;
  model_id: string | null;
  /** False when a stored default no longer resolves (stale provider/model). */
  is_valid: boolean;
  provider_display_name: string | null;
  /** ADR 0070: esfuerzo de razonamiento del default de plataforma. */
  reasoning_effort: string | null;
}

/** The effective model for the tenant's assistant (resolved with inheritance). */
export function getAssistantModel(): Promise<AssistantModel> {
  return apiFetch<AssistantModel>("/assistant/model");
}

/** Active providers + their catalogued models — the tenant dropdown source. */
export function getAssistantModelOptions(): Promise<AssistantModelOptions> {
  return apiFetch<AssistantModelOptions>("/assistant/model/options");
}

/** Set the tenant model override (validated server-side; 422 if invalid). */
export function setAssistantModel(
  providerId: string,
  modelId: string,
  reasoningEffort = "off",
): Promise<AssistantModel> {
  return apiFetch<AssistantModel>("/assistant/model", {
    method: "PUT",
    body: { provider_id: providerId, model_id: modelId, reasoning_effort: reasoningEffort },
  });
}

/** Clear the tenant override (the assistant inherits the platform default). */
export function clearAssistantModel(): Promise<AssistantModel> {
  return apiFetch<AssistantModel>("/assistant/model", {
    method: "PUT",
    body: { provider_id: null, model_id: null },
  });
}

/** The platform default model selection (System Admin). */
export function getAssistantDefaultModel(): Promise<AssistantDefaultModel> {
  return apiFetch<AssistantDefaultModel>("/assistant/default-model");
}

/** Dropdown source for the System-Admin default control (no tenant needed). */
export function getAssistantDefaultModelOptions(): Promise<AssistantModelOptions> {
  return apiFetch<AssistantModelOptions>("/assistant/default-model/options");
}

/** Set the platform default model (System Admin; validated server-side). */
export function setAssistantDefaultModel(
  providerId: string,
  modelId: string,
  reasoningEffort = "off",
): Promise<AssistantDefaultModel> {
  return apiFetch<AssistantDefaultModel>("/assistant/default-model", {
    method: "PUT",
    body: { provider_id: providerId, model_id: modelId, reasoning_effort: reasoningEffort },
  });
}

/** Clear the platform default (System Admin). */
export function clearAssistantDefaultModel(): Promise<AssistantDefaultModel> {
  return apiFetch<AssistantDefaultModel>("/assistant/default-model", {
    method: "PUT",
    body: { provider_id: null, model_id: null },
  });
}
