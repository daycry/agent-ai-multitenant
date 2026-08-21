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
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang, type Lang } from "@/lib/lang-context";
import {
  chatModeLabel,
  composeEffectivePrompt,
  draftFromConfig,
  isProviderKind,
  PROVIDER_LABEL,
  resolvePromptSource,
  UNAVAILABLE_LABEL,
  validateDraft,
  type ChatModeOption,
  type ModelConfig,
  type ModelConfigDraft,
  type SystemPrompts,
} from "@/lib/persona/persona";

import { ProviderModelSelects, useProviderOptions } from "./provider-model-selects";

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
  const t = useT("capability");
  const { data: modes } = useChatModes();
  const draft = useMemo(() => draftFromConfig(modelConfig), [modelConfig]);
  // ADR 0082: resolvemos el provider_id a su nombre concreto (ollama-cloud vs
  // local) para el resumen; fallback a la etiqueta del kind si es legacy o la
  // fila ya no existe.
  const { data: provOpts } = useProviderOptions();
  const selectedProvider = (provOpts?.providers ?? []).find((p) => p.id === draft.provider_id);
  const providerSummary = selectedProvider
    ? `${selectedProvider.display_name} (${selectedProvider.kind})`
    : isProviderKind(draft.provider)
      ? PROVIDER_LABEL[draft.provider][lang]
      : draft.provider || null;

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

  const modelConfigured = Boolean(draft.provider_id || draft.provider) && Boolean(draft.model);

  return (
    <Card data-testid="persona-section">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          <span className="inline-flex items-center gap-2">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            {t("personaTitle")}
          </span>
        </CardTitle>
        <p className="text-muted-foreground text-xs">{t("personaDescription")}</p>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Modelo configurado (honesto: si no hay provider/model lo dice). */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="persona-model-summary">
          <SummaryField
            label={t("fieldProvider")}
            value={modelConfigured ? providerSummary : null}
            fallback={t("personaNotConfigured")}
            testid="persona-summary-provider"
          />
          <SummaryField
            label={t("fieldModel")}
            value={draft.model || null}
            fallback={t("personaNotConfigured")}
            testid="persona-summary-model"
          />
          <SummaryField
            label={t("fieldTemperature")}
            value={String(draft.temperature)}
            testid="persona-summary-temperature"
          />
        </div>

        {/* Vista del PROMPT EFECTIVO: rol + modo de chat. */}
        <div className="space-y-2" data-testid="persona-effective-prompt">
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="persona-mode-select">{t("personaCombineWithMode")}</Label>
              <Select
                id="persona-mode-select"
                value={modeName}
                onChange={(e) => setModeName(e.target.value)}
                data-testid="persona-mode-select"
                className="w-56"
              >
                <option value="">{t("personaRoleOnly")}</option>
                {(modes ?? []).map((m) => (
                  <option key={m.name} value={m.name} disabled={!m.available}>
                    {chatModeLabel(m, lang)}
                    {!m.available ? ` — ${UNAVAILABLE_LABEL[lang]}` : ""}
                  </option>
                ))}
              </Select>
            </div>
            <Badge variant="muted" data-testid="persona-role-badge">
              {t("personaRoleLabel")}: {role}
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
              {t("personaCustomUnavailable", { label: UNAVAILABLE_LABEL[lang] })}
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
              {t("personaNoPrompt")}
            </p>
          )}
          {role_prompt.origin === "flat" && (
            <p className="text-muted-foreground text-xs" data-testid="persona-prompt-origin-flat">
              {t("personaPromptOriginFlat")}
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

  // ADR 0082: selección por PROVEEDOR CONCRETO mediante el componente reutilizable
  // (mismo patrón/endpoint que el resto de selectores de modelo). El draft espeja
  // exactamente `ProviderModelValue` (provider_id + provider(kind) + model + …).
  return (
    <ProviderModelSelects
      value={draft}
      onChange={onChange}
      idPrefix={idPrefix}
      errorFor={errorFor}
    />
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
  const t = useT("capability");
  return (
    <div className="space-y-3" data-testid={`${idPrefix}-prompt-fields`}>
      <p className="text-muted-foreground text-xs">{t("personaPromptsHelp")}</p>
      <PromptLangField
        langTag="es"
        // "System prompt (ES)" es igual en los dos idiomas: nombra el campo del
        // JSON, no una frase. No pasa por el diccionario.
        label="System prompt (ES)"
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
  const t = useT("capability");
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={`${idPrefix}-prompt-${langTag}`}>
        {label}
        <Tooltip
          content={langTag === "es" ? t("personaPromptStoredEs") : t("personaPromptStoredEn")}
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
