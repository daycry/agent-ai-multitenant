"use client";

/**
 * Sección Persona (SER) del agente (Plan 06.17 task_06_17_11).
 *
 * La pata **SER** del modelo mental SABER/RECORDAR/SER/HACER
 * (`docs/04-reference/training-model.md`): quién es el agente y cómo se comporta.
 * Esta sección materializa tres cosas que el plan exige:
 *
 *   1. **Selector de proveedor/modelo/temperatura** del catálogo CERRADO (ADR
 *      0021/0055): el `<select>` SOLO ofrece los 4 proveedores. La validación de
 *      rango/catálogo vive en `@/lib/persona/persona` (testeada aislada); el
 *      backend revalida (422).
 *   2. **Vista "prompt efectivo"** que combina el prompt del rol con el del modo
 *      de chat elegido (consumido de `GET /chat-modes`, NO hardcodeado). El modo
 *      `custom` se muestra "No disponible aún" (honestidad de estado, regla 4).
 *   3. **Edición bilingüe es/en** del `system_prompt` sobre la MISMA fuente que
 *      lee la tarjeta de la lista (`model_config.system_prompts`), cerrando la
 *      colisión lista (bilingüe) vs detalle (plano).
 *
 * Reutiliza el sistema de diseño existente (Card/Badge/Select/Tooltip shadcn) y
 * el verbo único "Editar" para la persona. NO recalcula lógica: deriva/valida/
 * compone delegando en el módulo puro `persona.ts`.
 *
 * Esta sección es de SOLO LECTURA (vista del set efectivo + prompt efectivo). La
 * edición real (proveedor/modelo/temperatura/prompts) la hacen los diálogos de
 * alta/edición de agente, que importan los CONTROLES reutilizables que este
 * módulo exporta (`PersonaModelFields`, `PersonaPromptFields`).
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Info, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang, type Lang } from "@/lib/lang-context";
import {
  chatModeLabel,
  composeEffectivePrompt,
  draftFromConfig,
  PROVIDER_KINDS,
  PROVIDER_LABEL,
  resolvePromptSource,
  TEMPERATURE_MAX,
  TEMPERATURE_MIN,
  UNAVAILABLE_LABEL,
  validateDraft,
  type ChatModeOption,
  type ModelConfig,
  type ModelConfigDraft,
  type SystemPrompts,
} from "@/lib/persona/persona";

// ---------------------------------------------------------------------------
// Hook: catálogo de modos de chat (GET /chat-modes) — fuente única del prompt
// de modo. Compartido por la vista del prompt efectivo.
// ---------------------------------------------------------------------------
function useChatModes() {
  return useQuery<ChatModeOption[], ApiError>({
    queryKey: ["chat-modes"],
    queryFn: () => apiFetch<ChatModeOption[]>("/chat-modes"),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60 * 1000,
  });
}

// ===========================================================================
// Vista de la sección Persona (read-only)
// ===========================================================================
interface PersonaSectionProps {
  /** model_config del agente (alias JSON de llm_config). */
  modelConfig: ModelConfig | null | undefined;
  /** Campo plano legacy `system_prompt` (fallback de la fuente única). */
  systemPrompt: string | null | undefined;
  /** El rol del agente, para encabezar la vista. */
  role: string;
}

export function PersonaSection({ modelConfig, systemPrompt, role }: PersonaSectionProps) {
  const { lang } = useLang();
  const { data: modes } = useChatModes();
  const draft = useMemo(() => draftFromConfig(modelConfig), [modelConfig]);

  const availableModes = useMemo(() => (modes ?? []).filter((m) => m.available), [modes]);
  const [modeName, setModeName] = useState<string>("");

  const selectedMode = useMemo<ChatModeOption | null>(
    () => availableModes.find((m) => m.name === modeName) ?? null,
    [availableModes, modeName],
  );

  const role_prompt = resolvePromptSource(modelConfig, systemPrompt, lang);
  const effective = useMemo(
    () =>
      composeEffectivePrompt({
        cfg: modelConfig,
        flatPrompt: systemPrompt,
        mode: selectedMode,
        lang,
      }),
    [modelConfig, systemPrompt, selectedMode, lang],
  );

  const modelConfigured = Boolean(draft.provider) && Boolean(draft.model);

  return (
    <Card data-testid="persona-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          <span className="inline-flex items-center gap-2">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            {lang === "es" ? "SER · Persona" : "BE · Persona"}
          </span>
        </CardTitle>
        <p className="text-muted-foreground text-xs">
          {lang === "es"
            ? "Quién es el agente: proveedor, modelo, temperatura y el prompt efectivo (rol + modo)."
            : "Who the agent is: provider, model, temperature and the effective prompt (role + mode)."}
        </p>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Modelo configurado (honesto: si no hay provider/model lo dice). */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="persona-model-summary">
          <SummaryField
            label={lang === "es" ? "Proveedor" : "Provider"}
            value={modelConfigured ? PROVIDER_LABEL[draft.provider][lang] : null}
            fallback={lang === "es" ? "No configurado" : "Not configured"}
            testid="persona-summary-provider"
          />
          <SummaryField
            label={lang === "es" ? "Modelo" : "Model"}
            value={draft.model || null}
            fallback={lang === "es" ? "No configurado" : "Not configured"}
            testid="persona-summary-model"
          />
          <SummaryField
            label={lang === "es" ? "Temperatura" : "Temperature"}
            value={String(draft.temperature)}
            testid="persona-summary-temperature"
          />
        </div>

        {/* Vista del PROMPT EFECTIVO: rol + modo de chat. */}
        <div className="space-y-2" data-testid="persona-effective-prompt">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="persona-mode-select">
                {lang === "es" ? "Combinar con el modo" : "Combine with mode"}
              </Label>
              <Select
                id="persona-mode-select"
                value={modeName}
                onChange={(e) => setModeName(e.target.value)}
                data-testid="persona-mode-select"
                className="w-56"
              >
                <option value="">{lang === "es" ? "Solo el rol" : "Role only"}</option>
                {(modes ?? []).map((m) => (
                  <option key={m.name} value={m.name} disabled={!m.available}>
                    {chatModeLabel(m, lang)}
                    {!m.available ? ` — ${UNAVAILABLE_LABEL[lang]}` : ""}
                  </option>
                ))}
              </Select>
            </div>
            <Badge variant="muted" data-testid="persona-role-badge">
              {lang === "es" ? "Rol" : "Role"}: {role}
            </Badge>
          </div>

          {/* El modo custom, si existe, se anuncia explícitamente "No disponible aún". */}
          {(modes ?? []).some((m) => m.name === "custom" && !m.available) && (
            <p
              className="text-muted-foreground inline-flex items-center gap-1 text-xs"
              data-testid="persona-custom-unavailable"
              role="note"
            >
              <Info className="h-3 w-3" aria-hidden="true" />
              {lang === "es"
                ? `El modo personalizado está "${UNAVAILABLE_LABEL.es}".`
                : `The custom mode is "${UNAVAILABLE_LABEL.en}".`}
            </p>
          )}

          {effective.combined.trim() ? (
            <pre
              className="bg-muted/40 max-h-72 overflow-auto whitespace-pre-wrap rounded p-3 text-xs"
              data-testid="persona-effective-prompt-text"
              data-origin={effective.roleOrigin}
            >
              {effective.combined}
            </pre>
          ) : (
            <p
              className="bg-warning-soft text-warning-soft-foreground rounded p-3 text-xs"
              data-testid="persona-no-prompt"
              role="status"
            >
              {lang === "es"
                ? "Sin system prompt definido. Edita la persona para añadir uno (es/en)."
                : "No system prompt defined. Edit the persona to add one (es/en)."}
            </p>
          )}
          {role_prompt.origin === "flat" && (
            <p className="text-muted-foreground text-xs" data-testid="persona-prompt-origin-flat">
              {lang === "es"
                ? "Prompt heredado del campo plano legacy; edita la persona para migrarlo a es/en."
                : "Prompt inherited from the legacy flat field; edit the persona to migrate it to es/en."}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function SummaryField({
  label,
  value,
  fallback,
  testid,
}: {
  label: string;
  value: string | null;
  fallback?: string;
  testid: string;
}) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      {value ? (
        <p className="font-medium" data-testid={testid}>
          {value}
        </p>
      ) : (
        <p className="text-warning-soft-foreground font-medium" data-testid={testid}>
          {fallback}
        </p>
      )}
    </div>
  );
}

// ===========================================================================
// CONTROLES reutilizables para los diálogos de alta/edición
// ===========================================================================

/**
 * Selector proveedor/modelo/temperatura. El `<select>` de proveedor SOLO ofrece
 * los 4 del catálogo cerrado (ADR 0021). Muestra errores de validación inline
 * (catálogo / rango) usando el módulo puro `persona.ts`.
 */
export function PersonaModelFields({
  draft,
  onChange,
  idPrefix = "persona",
}: {
  draft: ModelConfigDraft;
  onChange: (next: ModelConfigDraft) => void;
  idPrefix?: string;
}) {
  const { lang } = useLang();
  const errors = useMemo(() => validateDraft(draft, lang), [draft, lang]);
  const errorFor = (field: "provider" | "model" | "temperature") =>
    errors.find((e) => e.field === field)?.message ?? null;

  // Modelos seleccionables por proveedor (catálogo cerrado, GET /agents/model-options).
  // Si el proveedor elegido tiene modelos activos → dropdown; si no (proveedor sin
  // backend activo o aún cargando) → input de texto, para no bloquear la edición.
  const optionsQuery = useQuery({
    queryKey: ["agent-model-options"],
    queryFn: () => apiFetch<{ by_kind: Record<string, string[]> }>("/agents/model-options"),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
  });
  const kindModels = optionsQuery.data?.by_kind?.[draft.provider] ?? [];
  // Si el modelo actual no está en la lista (legacy/custom), lo anteponemos para no perderlo.
  const modelOptions =
    draft.model && !kindModels.includes(draft.model) ? [draft.model, ...kindModels] : kindModels;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid={`${idPrefix}-model-fields`}>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-provider`}>{lang === "es" ? "Proveedor" : "Provider"}</Label>
        <Select
          id={`${idPrefix}-provider`}
          value={draft.provider}
          onChange={(e) =>
            // Al cambiar de proveedor, reseteamos el modelo: el dropdown se
            // repuebla con los del nuevo proveedor (el usuario elige uno válido).
            onChange({
              ...draft,
              provider: e.target.value as ModelConfigDraft["provider"],
              model: "",
            })
          }
          data-testid={`${idPrefix}-provider`}
        >
          {PROVIDER_KINDS.map((p) => (
            <option key={p} value={p}>
              {PROVIDER_LABEL[p][lang]}
            </option>
          ))}
        </Select>
        {errorFor("provider") && (
          <p
            className="text-danger-soft-foreground text-xs"
            data-testid={`${idPrefix}-provider-error`}
          >
            {errorFor("provider")}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-model`}>{lang === "es" ? "Modelo" : "Model"}</Label>
        {modelOptions.length > 0 ? (
          <Select
            id={`${idPrefix}-model`}
            value={draft.model}
            onChange={(e) => onChange({ ...draft, model: e.target.value })}
            data-testid={`${idPrefix}-model`}
          >
            <option value="">{lang === "es" ? "— Selecciona —" : "— Select —"}</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        ) : (
          // Fallback: proveedor sin modelos activos (o aún cargando) → texto libre.
          <Input
            id={`${idPrefix}-model`}
            value={draft.model}
            onChange={(e) => onChange({ ...draft, model: e.target.value })}
            placeholder="claude-sonnet-4"
            data-testid={`${idPrefix}-model`}
          />
        )}
        {errorFor("model") && (
          <p
            className="text-danger-soft-foreground text-xs"
            data-testid={`${idPrefix}-model-error`}
          >
            {errorFor("model")}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-temperature`}>
          {lang === "es" ? "Temperatura" : "Temperature"}
        </Label>
        <Input
          id={`${idPrefix}-temperature`}
          type="number"
          min={TEMPERATURE_MIN}
          max={TEMPERATURE_MAX}
          step={0.1}
          value={draft.temperature}
          onChange={(e) => onChange({ ...draft, temperature: Number(e.target.value) })}
          data-testid={`${idPrefix}-temperature`}
        />
        {errorFor("temperature") && (
          <p
            className="text-danger-soft-foreground text-xs"
            data-testid={`${idPrefix}-temperature-error`}
          >
            {errorFor("temperature")}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Edición bilingüe es/en del system prompt — la MISMA fuente que lee la tarjeta
 * de la lista (`model_config.system_prompts`). Dos textareas etiquetadas por
 * idioma; el llamante construye el `model_config` con `buildModelConfig`.
 */
export function PersonaPromptFields({
  prompts,
  onChange,
  idPrefix = "persona",
}: {
  prompts: SystemPrompts;
  onChange: (next: SystemPrompts) => void;
  idPrefix?: string;
}) {
  const { lang } = useLang();
  return (
    <div className="space-y-3" data-testid={`${idPrefix}-prompt-fields`}>
      <p className="text-muted-foreground text-xs">
        {lang === "es"
          ? "System prompt por idioma (ES + EN). Es la fuente única que muestran la tarjeta y el prompt efectivo."
          : "System prompt per language (ES + EN). Single source shown by the card and the effective prompt."}
      </p>
      <PromptLangField
        langTag="es"
        label={lang === "es" ? "System prompt (ES)" : "System prompt (ES)"}
        value={prompts.es ?? ""}
        onChange={(v) => onChange({ ...prompts, es: v })}
        idPrefix={idPrefix}
      />
      <PromptLangField
        langTag="en"
        label="System prompt (EN)"
        value={prompts.en ?? ""}
        onChange={(v) => onChange({ ...prompts, en: v })}
        idPrefix={idPrefix}
      />
    </div>
  );
}

function PromptLangField({
  langTag,
  label,
  value,
  onChange,
  idPrefix,
}: {
  langTag: Lang;
  label: string;
  value: string;
  onChange: (v: string) => void;
  idPrefix: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`${idPrefix}-prompt-${langTag}`}>
        {label}
        <Tooltip
          content={
            langTag === "es"
              ? "Se guarda en model_config.system_prompts.es"
              : "Stored in model_config.system_prompts.en"
          }
        >
          <TooltipTrigger className="ml-1 align-middle" aria-label={`${label} info`}>
            <Info className="text-muted-foreground inline h-3 w-3" aria-hidden="true" />
          </TooltipTrigger>
        </Tooltip>
      </Label>
      <textarea
        id={`${idPrefix}-prompt-${langTag}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={5}
        className="border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2"
        data-testid={`${idPrefix}-prompt-${langTag}`}
      />
    </div>
  );
}
