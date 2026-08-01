/**
 * Lógica PURA de la sección Persona (SER) del agente (Plan 06.17 task_06_17_11).
 *
 * La pata **SER** del modelo mental SABER/RECORDAR/SER/HACER
 * (`docs/04-reference/training-model.md`): quién es el agente y cómo se comporta
 * = `system_prompt` + `model_config` (proveedor/modelo/temperatura/prompts es+en)
 * + modo de chat. Este módulo NO toca React ni el DOM: deriva, valida y compone,
 * para testearlo aislado (`persona.test.ts`), igual que `lib/capability/hub.ts`,
 * `lib/memory/honesty.ts` y `lib/tools/taxonomy.ts`.
 *
 * Reglas del plan que materializa:
 *   - **Catálogo cerrado de proveedores** (ADR 0021/0055): el selector SOLO
 *     ofrece los CUATRO `claude_sdk` / `copilot` / `azure_foundry` / `ollama`.
 *     Esta lista es la fuente única de la UI; espeja `LLM_PROVIDER_KINDS` del
 *     backend (`api_server.db.llm_providers`). NUNCA inventar un quinto.
 *   - **Temperatura en rango** `[0, 2]` (mismo rango que valida el schema del
 *     agente, `MODEL_TEMPERATURE_MIN/MAX`).
 *   - **Prompt efectivo** = prompt del rol (system_prompt del agente, por idioma)
 *     + prompt del modo de chat seleccionado (built-in). El modo `custom` está
 *     "No disponible aún" (honestidad de estado, regla 4).
 *   - **Colisión lista vs detalle**: la tarjeta de la lista lee
 *     `model_config.system_prompts.{es,en}`; el detalle leía el campo plano
 *     `system_prompt`. La FUENTE ÚNICA es `model_config.system_prompts`; el
 *     campo plano es el fallback legacy. `resolvePromptSource` centraliza esa
 *     resolución para que lista y detalle muestren EXACTAMENTE lo mismo.
 */

import { pickLang, translate } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";

// ---------------------------------------------------------------------------
// Catálogo cerrado de proveedores (ADR 0021 / 0055) — fuente única de la UI.
// ---------------------------------------------------------------------------

/** Los CUATRO proveedores del catálogo cerrado (espeja `LLMProviderKind`). */
export type ProviderKind = "claude_sdk" | "copilot" | "azure_foundry" | "ollama";

/** Lista canónica y ordenada (Claude SDK primero: camino primario, ADR 0021). */
export const PROVIDER_KINDS: readonly ProviderKind[] = [
  "claude_sdk",
  "copilot",
  "azure_foundry",
  "ollama",
] as const;

/** Etiqueta amigable bilingüe de cada proveedor (no se renderiza el slug crudo). */
export const PROVIDER_LABEL: Record<ProviderKind, Record<Lang, string>> = {
  claude_sdk: { es: "Claude (suscripción)", en: "Claude (subscription)" },
  copilot: { es: "GitHub Copilot", en: "GitHub Copilot" },
  azure_foundry: { es: "Azure AI Foundry", en: "Azure AI Foundry" },
  ollama: { es: "Ollama (local)", en: "Ollama (local)" },
};

export function isProviderKind(value: string): value is ProviderKind {
  return (PROVIDER_KINDS as readonly string[]).includes(value);
}

/** Default seguro de código. Tras ADR 0082 la selección es por PROVEEDOR CONCRETO
 * (`provider_id`), así que no hay un provider_id por defecto: arranca vacío y el
 * operador elige una fila de `/agents/provider-options`. `provider` (kind) queda
 * como etiqueta del proveedor seleccionado. */
export const DEFAULT_MODEL_CONFIG: ModelConfigDraft = {
  provider_id: "",
  provider: "claude_sdk",
  model: "",
  // 0.1 alinea con el default del plugin de GitHub Copilot en VS Code.
  temperature: 0.1,
  reasoning_effort: "off",
};

// Rango de temperatura (mismo que `MODEL_TEMPERATURE_MIN/MAX` del backend).
export const TEMPERATURE_MIN = 0;
export const TEMPERATURE_MAX = 2;

// ---------------------------------------------------------------------------
// Shape del model_config (espeja la columna `model_config` del agente).
// ---------------------------------------------------------------------------

/** Sub-objeto bilingüe de prompts dentro de `model_config`. */
export interface SystemPrompts {
  es?: string;
  en?: string;
}

/**
 * El `model_config` del agente tal cual lo expone el backend (alias JSON de
 * `llm_config`). Solo tipamos lo que la UI de persona toca; otros campos
 * (p. ej. `system_prompts`) conviven sin que los borremos.
 */
export interface ModelConfig {
  /** Kind del proveedor (claude_sdk/…). Se conserva junto a `provider_id` para la
   * cadena de herencia + display + back-compat (ADR 0082). */
  provider?: string;
  /** Fila CONCRETA del proveedor elegida (ollama-local vs ollama-cloud). La
   * resolución prefiere esto; sin él, cae a kind→fila-más-nueva (ADR 0082). */
  provider_id?: string;
  model?: string;
  temperature?: number;
  /** Esfuerzo de razonamiento por proveedor (ADR 0070). "off"/ausente = sin razonar. */
  reasoning_effort?: string;
  system_prompts?: SystemPrompts;
  [key: string]: unknown;
}

/** El borrador que el formulario edita y envía. ADR 0082: la selección es por
 * PROVEEDOR CONCRETO (`provider_id`); `provider` es el kind de esa fila (para
 * herencia/validación/display). */
export interface ModelConfigDraft {
  /** Fila concreta elegida en `/agents/provider-options` ("" = ninguna aún). */
  provider_id: string;
  /** Kind del proveedor elegido (derivado de la fila); "" si aún no se eligió. */
  provider: string;
  model: string;
  temperature: number;
  /** Opción de razonamiento elegida; "off" = sin razonamiento (ADR 0070). */
  reasoning_effort: string;
}

// ---------------------------------------------------------------------------
// Validación del borrador (espeja `validate_model_config` del backend).
// ---------------------------------------------------------------------------

export interface ModelConfigError {
  /** Campo que falla, para anclar el mensaje en el control correcto. */
  field: "provider" | "model" | "temperature";
  message: string;
}

/**
 * Valida un borrador contra el catálogo cerrado + rango de temperatura. Devuelve
 * la lista de errores (vacía = válido). El backend revalida y devuelve 422; esta
 * validación de cliente da feedback inmediato, no reemplaza la del servidor.
 */
export function validateDraft(draft: ModelConfigDraft, lang: Lang): ModelConfigError[] {
  const errors: ModelConfigError[] = [];
  // ADR 0082: se elige un PROVEEDOR CONCRETO (provider_id). El kind lo gobierna la
  // fila (siempre ∈ catálogo cerrado por el CHECK de DB), así que validamos que se
  // haya seleccionado una fila, no el enum de kinds.
  if (!draft.provider_id.trim()) {
    errors.push({
      field: "provider",
      message: translate(lang, "persona", "errorProvider"),
    });
  }
  if (!draft.model.trim()) {
    errors.push({
      field: "model",
      message: translate(lang, "persona", "errorModelEmpty"),
    });
  }
  if (
    !Number.isFinite(draft.temperature) ||
    draft.temperature < TEMPERATURE_MIN ||
    draft.temperature > TEMPERATURE_MAX
  ) {
    errors.push({
      field: "temperature",
      message: translate(lang, "persona", "errorTemperature", {
        min: TEMPERATURE_MIN,
        max: TEMPERATURE_MAX,
      }),
    });
  }
  return errors;
}

/** Extrae un borrador editable a partir del `model_config` actual del agente.
 *
 * ADR 0082: lee `provider_id` (la fila concreta). Un config LEGACY (solo `provider`
 * kind, sin `provider_id`) arranca con `provider_id=""` → el selector queda sin
 * elegir y el operador re-selecciona la fila una vez (no se puede inferir qué fila
 * concreta era). `provider` (kind) se conserva como etiqueta. */
export function draftFromConfig(cfg: ModelConfig | null | undefined): ModelConfigDraft {
  const provider = cfg?.provider;
  const temperature = typeof cfg?.temperature === "number" ? cfg.temperature : undefined;
  return {
    provider_id:
      typeof cfg?.provider_id === "string" && cfg.provider_id.trim() ? cfg.provider_id : "",
    provider: provider && isProviderKind(provider) ? provider : DEFAULT_MODEL_CONFIG.provider,
    model: typeof cfg?.model === "string" && cfg.model.trim() ? cfg.model : "",
    temperature: temperature ?? DEFAULT_MODEL_CONFIG.temperature,
    reasoning_effort:
      typeof cfg?.reasoning_effort === "string" && cfg.reasoning_effort.trim()
        ? cfg.reasoning_effort
        : "off",
  };
}

// ---------------------------------------------------------------------------
// Colisión lista vs detalle del system prompt (fuente ÚNICA).
// ---------------------------------------------------------------------------

/** De dónde salió el prompt mostrado (para ser honestos sobre el origen). */
export type PromptOrigin = "bilingual" | "flat" | "none";

export interface ResolvedPrompt {
  /** El texto resuelto en el idioma pedido (cadena vacía si no hay). */
  text: string;
  origin: PromptOrigin;
}

/**
 * Resuelve el system prompt mostrado, en el idioma pedido, desde la FUENTE
 * ÚNICA `model_config.system_prompts` con fallback al campo plano `system_prompt`
 * legacy. Esto cierra la colisión "lista (bilingüe) vs detalle (plano)": ambas
 * vistas llaman aquí y muestran lo mismo. Si el idioma pedido no existe pero el
 * otro sí, cae al otro (mejor mostrar algo real que un hueco).
 */
export function resolvePromptSource(
  cfg: ModelConfig | null | undefined,
  flatPrompt: string | null | undefined,
  lang: Lang,
): ResolvedPrompt {
  const prompts = cfg?.system_prompts;
  if (prompts) {
    // `pickLang` es exactamente esto: el idioma pedido, y si viene vacío el
    // otro. Antes estaba escrito a mano aquí y en otros trece sitios.
    const text = pickLang(lang, { es: prompts.es ?? "", en: prompts.en ?? "" });
    if (text.trim()) return { text, origin: "bilingual" };
  }
  if (flatPrompt && flatPrompt.trim()) return { text: flatPrompt, origin: "flat" };
  return { text: "", origin: "none" };
}

// ---------------------------------------------------------------------------
// Catálogo de modos de chat (consumido de GET /chat-modes — NO hardcodear).
// ---------------------------------------------------------------------------

/** Espeja `ChatModeResponse` del backend (`schemas/conversations.py`). */
export interface ChatModeOption {
  name: string;
  label_es: string;
  label_en: string;
  system_prompt: string;
  available: boolean;
}

export function chatModeLabel(mode: ChatModeOption, lang: Lang): string {
  return pickLang(lang, { es: mode.label_es, en: mode.label_en });
}

// ---------------------------------------------------------------------------
// Prompt efectivo = prompt del rol (por idioma) + prompt del modo seleccionado.
// ---------------------------------------------------------------------------

export interface EffectivePromptInput {
  /** model_config del agente (fuente del prompt bilingüe). */
  cfg: ModelConfig | null | undefined;
  /** Campo plano legacy (fallback). */
  flatPrompt: string | null | undefined;
  /** El modo de chat elegido (o `null` si ninguno → solo rol). */
  mode: ChatModeOption | null;
  lang: Lang;
}

export interface EffectivePrompt {
  /** El prompt del rol resuelto (fuente única). */
  rolePrompt: string;
  roleOrigin: PromptOrigin;
  /** El prompt del modo (vacío si el modo no aplica / no disponible). */
  modePrompt: string;
  /** La composición final que recibe el LLM (rol + modo). */
  combined: string;
}

/** Encabezado bilingüe de cada bloque del prompt efectivo. */
const ROLE_HEADER: Record<Lang, string> = {
  es: "— Rol del agente —",
  en: "— Agent role —",
};
const MODE_HEADER: Record<Lang, string> = {
  es: "— Modo de chat —",
  en: "— Chat mode —",
};

/**
 * Compone el prompt EFECTIVO que el agente recibe: el prompt del rol seguido del
 * prompt del modo de chat. Un modo `available=false` (custom) NO contribuye texto
 * (honestidad: no se promete un comportamiento de modo que no existe). El orden
 * (rol primero, modo después) refleja cómo el agente loop apila el rol sobre el
 * frame del modo.
 */
export function composeEffectivePrompt(input: EffectivePromptInput): EffectivePrompt {
  const { cfg, flatPrompt, mode, lang } = input;
  const role = resolvePromptSource(cfg, flatPrompt, lang);
  const modePrompt = mode && mode.available ? mode.system_prompt.trim() : "";
  const parts: string[] = [];
  if (role.text.trim()) {
    parts.push(`${ROLE_HEADER[lang]}\n${role.text.trim()}`);
  }
  if (modePrompt) {
    parts.push(`${MODE_HEADER[lang]}\n${modePrompt}`);
  }
  return {
    rolePrompt: role.text,
    roleOrigin: role.origin,
    modePrompt,
    combined: parts.join("\n\n"),
  };
}

// ---------------------------------------------------------------------------
// Construcción del model_config a ENVIAR (preserva system_prompts bilingües).
// ---------------------------------------------------------------------------

export interface BuildConfigInput {
  /** El model_config actual (para no perder system_prompts ni claves extra). */
  current: ModelConfig | null | undefined;
  draft: ModelConfigDraft;
  /** Los prompts bilingües editados (lo que el usuario tecleó). */
  prompts: SystemPrompts;
}

/**
 * Construye el `model_config` a enviar en create/update. Parte del actual (para
 * conservar claves que la UI no edita), aplica el borrador (proveedor/modelo/
 * temperatura del catálogo) y reemplaza `system_prompts` con los prompts es/en
 * editados. Las cadenas vacías se omiten para no persistir "" como prompt.
 */
export function buildModelConfig(input: BuildConfigInput): ModelConfig {
  const { current, draft, prompts } = input;
  const next: ModelConfig = { ...(current ?? {}) };
  // ADR 0082: persistimos la fila concreta (provider_id) + su kind (provider). La
  // resolución prefiere provider_id; el kind se conserva para herencia/display.
  if (draft.provider_id.trim()) {
    next.provider_id = draft.provider_id;
  } else {
    delete next.provider_id;
  }
  next.provider = draft.provider;
  next.model = draft.model.trim();
  next.temperature = draft.temperature;

  // ADR 0070: persistir reasoning_effort solo si está activo; "off"/vacío lo
  // omite (y borra el heredado del `current` para no dejar un valor colgado).
  if (draft.reasoning_effort && draft.reasoning_effort !== "off") {
    next.reasoning_effort = draft.reasoning_effort;
  } else {
    delete next.reasoning_effort;
  }

  const system_prompts: SystemPrompts = {};
  if (prompts.es && prompts.es.trim()) system_prompts.es = prompts.es;
  if (prompts.en && prompts.en.trim()) system_prompts.en = prompts.en;
  if (system_prompts.es || system_prompts.en) {
    next.system_prompts = system_prompts;
  } else {
    delete next.system_prompts;
  }
  return next;
}

/** Etiqueta reutilizable "No disponible aún" (honestidad de estado, regla 4). */
export const UNAVAILABLE_LABEL: Record<Lang, string> = {
  es: "No disponible aún",
  en: "Not available yet",
};
