"use client";

/**
 * Sección "Modelo del chat" (Feature B) para Equipo y Proyecto. A diferencia de
 * `DefaultModelSection` (modelo de EJECUCIÓN, por kind), aquí el operador elige un
 * PROVEEDOR CONCRETO por nombre (fila activa: p.ej. "Ollama local" vs "Ollama cloud")
 * y uno de SUS modelos. Persiste `chat_model_config = {provider_id, model, temperature?,
 * reasoning_effort?}` (o `{}` para heredar el modelo de ejecución). Solo afecta al chat.
 *
 * El padre cablea la mutación (PUT /teams/{id} o /projects/{id} con `chat_model_config`).
 */

import { useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang } from "@/lib/lang-context";

interface ProviderOption {
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

export interface ChatModelConfig {
  provider_id?: string;
  model?: string;
  temperature?: number;
  reasoning_effort?: string;
}

export function ChatModelSection({
  value,
  onSave,
  pending = false,
  isReadOnly = false,
  idPrefix,
}: {
  /** chat_model_config actual. `{}`/sin provider_id = hereda el modelo de ejecución. */
  value: ChatModelConfig | null | undefined;
  onSave: (cfg: ChatModelConfig) => void;
  pending?: boolean;
  isReadOnly?: boolean;
  idPrefix: string;
}) {
  const { lang } = useLang();
  const t = (es: string, en: string) => (lang === "es" ? es : en);
  const pinned = Boolean(value?.provider_id && value?.model);

  const [inherit, setInherit] = useState(!pinned);
  const [providerId, setProviderId] = useState(value?.provider_id ?? "");
  const [model, setModel] = useState(value?.model ?? "");
  const [temperature, setTemperature] = useState(value?.temperature ?? 0.3);
  const [reasoning, setReasoning] = useState(value?.reasoning_effort ?? "off");

  const optsQuery = useQuery<ProviderOptionsResponse, ApiError>({
    queryKey: ["agent-provider-options"],
    queryFn: () => apiFetch<ProviderOptionsResponse>("/agents/provider-options"),
  });
  const providers = optsQuery.data?.providers ?? [];
  const selected = providers.find((p) => p.id === providerId);
  const models = selected?.models ?? [];
  const reasoningOpts = selected?.reasoning_options ?? [];
  // claude_sdk ignora temperature; los demás la envían.
  const tempApplies = selected ? selected.kind !== "claude_sdk" : true;

  const reasoningLabel = (opt: string) => (opt === "off" ? t("Desactivado", "Off") : opt);

  function handleSave() {
    if (inherit) {
      onSave({});
      return;
    }
    const cfg: ChatModelConfig = { provider_id: providerId, model };
    if (tempApplies) cfg.temperature = temperature;
    if (reasoning && reasoning !== "off") cfg.reasoning_effort = reasoning;
    onSave(cfg);
  }

  const canSave = inherit || Boolean(providerId && model);

  return (
    <Card data-testid={`${idPrefix}-chat-model`}>
      <CardHeader>
        <CardTitle>{t("Modelo del chat", "Chat model")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-muted-foreground text-sm">
          {t(
            "El proveedor y modelo con el que el equipo RESPONDE en el chat de " +
              "planificación. Vacío = usa el modelo de ejecución. Un proveedor no-agéntico " +
              "(Ollama/Azure) hace el chat más rápido que claude_sdk.",
            "The provider + model the team REPLIES with in the planning chat. Empty = use " +
              "the execution model. A non-agentic provider (Ollama/Azure) makes the chat " +
              "faster than claude_sdk.",
          )}
        </p>

        {isReadOnly ? (
          <p
            className="text-muted-foreground text-sm"
            data-testid={`${idPrefix}-chat-model-readonly`}
          >
            {pinned
              ? `${value?.provider_id} · ${value?.model}`
              : t("Hereda el modelo de ejecución.", "Inherits the execution model.")}
          </p>
        ) : (
          <>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={inherit}
                onChange={(e) => setInherit(e.target.checked)}
                data-testid={`${idPrefix}-chat-inherit`}
              />
              {t("Heredar el modelo de ejecución", "Inherit the execution model")}
            </label>

            {!inherit && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`${idPrefix}-chat-provider`}>{t("Proveedor", "Provider")}</Label>
                  <Select
                    id={`${idPrefix}-chat-provider`}
                    value={providerId}
                    onChange={(e) => {
                      setProviderId(e.target.value);
                      setModel(""); // cada proveedor tiene sus modelos
                      setReasoning("off");
                    }}
                    data-testid={`${idPrefix}-chat-provider`}
                  >
                    <option value="">{t("— Selecciona —", "— Select —")}</option>
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.display_name} ({p.kind})
                      </option>
                    ))}
                  </Select>
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`${idPrefix}-chat-model-select`}>{t("Modelo", "Model")}</Label>
                  {models.length > 0 ? (
                    <Select
                      id={`${idPrefix}-chat-model-select`}
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                      data-testid={`${idPrefix}-chat-model-select`}
                    >
                      <option value="">{t("— Selecciona —", "— Select —")}</option>
                      {models.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    <Input
                      id={`${idPrefix}-chat-model-select`}
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                      placeholder={t("nombre del modelo", "model name")}
                      data-testid={`${idPrefix}-chat-model-select`}
                    />
                  )}
                </div>

                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`${idPrefix}-chat-temperature`}>
                    {t("Temperatura", "Temperature")}
                  </Label>
                  <Input
                    id={`${idPrefix}-chat-temperature`}
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={temperature}
                    onChange={(e) => setTemperature(Number(e.target.value))}
                    disabled={!tempApplies}
                    data-testid={`${idPrefix}-chat-temperature`}
                  />
                </div>

                {reasoningOpts.length > 0 && (
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor={`${idPrefix}-chat-reasoning`}>
                      {t("Razonamiento", "Reasoning")}
                    </Label>
                    <Select
                      id={`${idPrefix}-chat-reasoning`}
                      value={reasoning}
                      onChange={(e) => setReasoning(e.target.value)}
                      data-testid={`${idPrefix}-chat-reasoning`}
                    >
                      {reasoningOpts.map((opt) => (
                        <option key={opt} value={opt}>
                          {reasoningLabel(opt)}
                        </option>
                      ))}
                    </Select>
                  </div>
                )}
              </div>
            )}

            <Button
              type="button"
              onClick={handleSave}
              disabled={pending || !canSave}
              data-testid={`${idPrefix}-chat-model-save`}
            >
              {pending
                ? t("Guardando…", "Saving…")
                : t("Guardar modelo del chat", "Save chat model")}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
