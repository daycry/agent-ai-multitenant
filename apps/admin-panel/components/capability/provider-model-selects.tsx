"use client";

/**
 * Selector REUTILIZABLE de proveedor+modelo (+temperatura+razonamiento) por
 * PROVEEDOR CONCRETO (ADR 0082). Fuente única de la UI de selección de modelo:
 * consume `GET /agents/provider-options` (cada fila activa: ollama-local vs
 * ollama-cloud, etc.), guarda `provider_id` (la fila) + `provider` (su kind, para
 * herencia/display) + model + temperature + reasoning_effort.
 *
 * Lo usan persona (agente/equipo/adopt) y el resto de sitios de selección de
 * modelo. Reemplaza el patrón viejo "por kind" (`/agents/model-options`, solo la
 * fila más nueva) que impedía elegir, p.ej., ollama-cloud.
 */

import { useQuery } from "@tanstack/react-query";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang } from "@/lib/lang-context";
import { reasoningLabel as labelFor, selectableReasoningOptions } from "@/lib/model-selection";

export interface ProviderOption {
  id: string;
  kind: string;
  display_name: string;
  slug: string | null;
  models: string[];
  reasoning_options: string[];
}

interface ProviderOptionsResponse {
  providers: ProviderOption[];
}

/** Query compartida de proveedores concretos activos (caché por queryKey). */
export function useProviderOptions() {
  return useQuery<ProviderOptionsResponse, ApiError>({
    queryKey: ["agent-provider-options"],
    queryFn: () => apiFetch<ProviderOptionsResponse>("/agents/provider-options"),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
  });
}

/** Valor que el selector edita (espeja las claves de model_config que toca). */
export interface ProviderModelValue {
  /** Fila concreta elegida ("" = ninguna aún). */
  provider_id: string;
  /** Kind de la fila elegida (para herencia/display). */
  provider: string;
  model: string;
  temperature: number;
  reasoning_effort: string;
}

export function ProviderModelSelects({
  value,
  onChange,
  idPrefix,
  errorFor,
}: {
  value: ProviderModelValue;
  onChange: (next: ProviderModelValue) => void;
  idPrefix: string;
  /** Errores de validación a anclar inline (opcional; persona los pasa). */
  errorFor?: (field: "provider" | "model" | "temperature") => string | null;
}) {
  const { lang } = useLang();
  const t = (es: string, en: string) => (lang === "es" ? es : en);

  const q = useProviderOptions();
  const providers = q.data?.providers ?? [];
  const selected = providers.find((p) => p.id === value.provider_id);
  const models = selected?.models ?? [];
  // Si el modelo guardado no está en la lista (legacy/custom), lo anteponemos.
  const modelOptions =
    value.model && !models.includes(value.model) ? [value.model, ...models] : models;
  const reasoningSelectable = selectableReasoningOptions(
    value.reasoning_effort,
    selected?.reasoning_options ?? [],
  );
  // claude_sdk ignora temperature; los demás la envían.
  const tempApplies = selected ? selected.kind !== "claude_sdk" : true;
  const reasoningLabel = (opt: string) => labelFor(opt, t("Desactivado", "Off"));
  const err = (field: "provider" | "model" | "temperature") => errorFor?.(field) ?? null;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid={`${idPrefix}-model-fields`}>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-provider`}>{t("Proveedor", "Provider")}</Label>
        <Select
          id={`${idPrefix}-provider`}
          value={value.provider_id}
          onChange={(e) => {
            const row = providers.find((p) => p.id === e.target.value);
            // Al cambiar de proveedor: fijamos provider_id + su kind, y reseteamos
            // modelo y razonamiento (cada fila tiene sus opciones).
            onChange({
              ...value,
              provider_id: e.target.value,
              provider: row?.kind ?? value.provider,
              model: "",
              reasoning_effort: "off",
            });
          }}
          data-testid={`${idPrefix}-provider`}
        >
          <option value="">{t("— Selecciona —", "— Select —")}</option>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.display_name} ({p.kind})
            </option>
          ))}
        </Select>
        {err("provider") && (
          <p
            className="text-danger-soft-foreground text-xs"
            data-testid={`${idPrefix}-provider-error`}
          >
            {err("provider")}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-model`}>{t("Modelo", "Model")}</Label>
        {modelOptions.length > 0 ? (
          <Select
            id={`${idPrefix}-model`}
            value={value.model}
            onChange={(e) => onChange({ ...value, model: e.target.value })}
            data-testid={`${idPrefix}-model`}
          >
            <option value="">{t("— Selecciona —", "— Select —")}</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        ) : (
          <Input
            id={`${idPrefix}-model`}
            value={value.model}
            onChange={(e) => onChange({ ...value, model: e.target.value })}
            placeholder={t("nombre del modelo", "model name")}
            data-testid={`${idPrefix}-model`}
          />
        )}
        {err("model") && (
          <p
            className="text-danger-soft-foreground text-xs"
            data-testid={`${idPrefix}-model-error`}
          >
            {err("model")}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-temperature`}>{t("Temperatura", "Temperature")}</Label>
        <Input
          id={`${idPrefix}-temperature`}
          type="number"
          min={0}
          max={2}
          step={0.1}
          value={value.temperature}
          onChange={(e) => onChange({ ...value, temperature: Number(e.target.value) })}
          disabled={!tempApplies}
          data-testid={`${idPrefix}-temperature`}
        />
        {!tempApplies && (
          <p className="text-muted-foreground text-xs" data-testid={`${idPrefix}-temperature-na`}>
            {t(
              "No aplica a Claude (el SDK no la expone)",
              "Not applicable to Claude (the SDK does not expose it)",
            )}
          </p>
        )}
        {tempApplies && err("temperature") && (
          <p
            className="text-danger-soft-foreground text-xs"
            data-testid={`${idPrefix}-temperature-error`}
          >
            {err("temperature")}
          </p>
        )}
      </div>

      {reasoningSelectable.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${idPrefix}-reasoning`}>{t("Razonamiento", "Reasoning")}</Label>
          <Select
            id={`${idPrefix}-reasoning`}
            value={value.reasoning_effort}
            onChange={(e) => onChange({ ...value, reasoning_effort: e.target.value })}
            data-testid={`${idPrefix}-reasoning`}
          >
            {reasoningSelectable.map((opt) => (
              <option key={opt} value={opt}>
                {reasoningLabel(opt)}
              </option>
            ))}
          </Select>
        </div>
      )}
    </div>
  );
}
