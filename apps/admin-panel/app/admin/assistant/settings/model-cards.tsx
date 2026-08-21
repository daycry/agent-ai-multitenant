"use client";

/**
 * Asistente personal — selección de MODELO LLM (ADR 0053).
 *
 * Dos tarjetas:
 *   - <AssistantModelCard>     — Tenant Admin: el modelo de SU asistente
 *     (override que hereda del default de plataforma). GET/PUT /assistant/model
 *     + GET /assistant/model/options.
 *   - <PlatformDefaultModelCard> — System Admin: el modelo por defecto de la
 *     plataforma que heredan los tenants sin override. GET/PUT
 *     /assistant/default-model + GET /assistant/default-model/options.
 *
 * El BACKEND valida la selección contra el catálogo cerrado (provider activo +
 * modelo catalogado) y hace el gating; la UI solo lo refleja.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cpu, Globe } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import { useT, type Translator } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";
import {
  clearAssistantDefaultModel,
  clearAssistantModel,
  getAssistantDefaultModel,
  getAssistantDefaultModelOptions,
  getAssistantModel,
  getAssistantModelOptions,
  setAssistantDefaultModel,
  setAssistantModel,
  type AssistantDefaultModel,
  type AssistantModel,
  type AssistantModelOption,
  type AssistantModelOptions,
} from "@/lib/assistant";

function modelsFor(providers: AssistantModelOption[], providerId: string): string[] {
  return providers.find((p) => p.provider_id === providerId)?.models ?? [];
}

/** Shared provider + model selects. ``providerId === ""`` means "none picked".
 * The model list comes from the provider's catalogue + synced models; a System
 * Admin populates it from "Proveedores LLM" → "Sincronizar modelos". */

/**
 * Etiqueta de un nivel de razonamiento.
 *
 * Solo `off` se traduce: `low`/`medium`/`high`/`xhigh`/`max` son los valores
 * del enum que viajan a la API y que el operador ve en los logs, igual que los
 * `guardrail_type` de `guardrails`.
 */
function reasoningLabel(opt: string, t: Translator<"assistantModel">): string {
  return opt === "off" ? t("reasoningOff") : opt;
}

function ProviderModelSelects({
  idPrefix,
  providers,
  providerId,
  modelId,
  reasoningByKind,
  reasoningEffort,
  onProviderChange,
  onModelChange,
  onReasoningChange,
  disabled,
}: {
  idPrefix: string;
  providers: AssistantModelOption[];
  providerId: string;
  modelId: string;
  reasoningByKind: Record<string, string[]>;
  reasoningEffort: string;
  onProviderChange: (next: string) => void;
  onModelChange: (next: string) => void;
  onReasoningChange: (next: string) => void;
  disabled: boolean;
}) {
  const t = useT("assistantModel");
  const models = providerId ? modelsFor(providers, providerId) : [];
  // Opciones de razonamiento del kind del proveedor elegido (ADR 0070).
  const kind = providers.find((p) => p.provider_id === providerId)?.kind;
  const reasoningOptions = kind ? (reasoningByKind[kind] ?? []) : [];
  const reasoningSelectable =
    reasoningEffort && reasoningEffort !== "off" && !reasoningOptions.includes(reasoningEffort)
      ? [reasoningEffort, ...reasoningOptions]
      : reasoningOptions;
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-1.5">
        <Label htmlFor={`${idPrefix}-provider`}>{t("fieldProvider")}</Label>
        <Select
          id={`${idPrefix}-provider`}
          data-testid={`${idPrefix}-provider`}
          value={providerId}
          disabled={disabled}
          onChange={(e) => onProviderChange(e.target.value)}
        >
          <option value="">{t("pickProvider")}</option>
          {providers.map((p) => (
            <option key={p.provider_id} value={p.provider_id}>
              {p.display_name} ({p.slug})
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${idPrefix}-model`}>{t("fieldModel")}</Label>
        <Select
          id={`${idPrefix}-model`}
          data-testid={`${idPrefix}-model`}
          value={modelId}
          disabled={disabled || !providerId || models.length === 0}
          onChange={(e) => onModelChange(e.target.value)}
        >
          <option value="">
            {!providerId
              ? t("pickProviderFirst")
              : models.length === 0
                ? t("noModels")
                : t("pickModel")}
          </option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
      </div>
      {reasoningOptions.length > 0 ? (
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-reasoning`}>{t("fieldReasoning")}</Label>
          <Select
            id={`${idPrefix}-reasoning`}
            data-testid={`${idPrefix}-reasoning`}
            value={reasoningEffort}
            disabled={disabled || !providerId}
            onChange={(e) => onReasoningChange(e.target.value)}
          >
            {reasoningSelectable.map((o) => (
              <option key={o} value={o}>
                {reasoningLabel(o, t)}
              </option>
            ))}
          </Select>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tenant Admin — the assistant's model (override → inherits platform default)
// ---------------------------------------------------------------------------
export function AssistantModelCard({ enabled }: { enabled: boolean }) {
  const t = useT("assistantModel");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState("off");
  const [seeded, setSeeded] = useState(false);

  const optionsQuery = useQuery<AssistantModelOptions, ApiError>({
    queryKey: ["assistant-model-options"],
    queryFn: getAssistantModelOptions,
    enabled,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const currentQuery = useQuery<AssistantModel, ApiError>({
    queryKey: ["assistant-model"],
    queryFn: getAssistantModel,
    enabled,
    refetchOnWindowFocus: false,
    retry: false,
  });

  // Seed the selects from the stored override (only when one exists — a tenant
  // inheriting the default starts with an empty picker).
  useEffect(() => {
    if (seeded || !currentQuery.data) return;
    if (currentQuery.data.has_tenant_override && currentQuery.data.provider_id) {
      setProviderId(currentQuery.data.provider_id);
      setModelId(currentQuery.data.model_id ?? "");
      setReasoningEffort(currentQuery.data.reasoning_effort ?? "off");
    }
    setSeeded(true);
  }, [seeded, currentQuery.data]);

  const providers = optionsQuery.data?.providers ?? [];
  const reasoningByKind = optionsQuery.data?.reasoning_by_kind ?? {};

  const saveMutation = useMutation<AssistantModel, ApiError>({
    mutationFn: () => setAssistantModel(providerId, modelId, reasoningEffort),
    onSuccess: (data) => {
      queryClient.setQueryData(["assistant-model"], data);
    },
  });
  const clearMutation = useMutation<AssistantModel, ApiError>({
    mutationFn: clearAssistantModel,
    onSuccess: (data) => {
      queryClient.setQueryData(["assistant-model"], data);
      setProviderId("");
      setModelId("");
      setReasoningEffort("off");
      saveMutation.reset();
    },
  });

  const current = currentQuery.data;
  const busy = saveMutation.isPending || clearMutation.isPending;

  return (
    <Card className="mt-6" data-testid="assistant-model-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cpu className="h-5 w-5" />
          {t("tenantTitle")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {!enabled ? (
          <p className="text-muted-foreground text-sm">{t("tenantLocked")}</p>
        ) : optionsQuery.isLoading || currentQuery.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            {t("loading")}
          </p>
        ) : (
          <>
            <p className="text-muted-foreground text-sm" data-testid="assistant-model-effective">
              {current?.source === "tenant_override" ? (
                <>
                  {t("effectiveOverride")}{" "}
                  <strong>
                    {current.provider_display_name} · {current.model_id}
                  </strong>
                </>
              ) : current?.source === "platform_default" ? (
                <>
                  {t("effectiveInherited")}{" "}
                  <strong>
                    {current.provider_display_name} · {current.model_id}
                  </strong>
                </>
              ) : (
                t("effectiveNone")
              )}
            </p>

            {providers.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("noProvidersTenant")}</p>
            ) : (
              <>
                <ProviderModelSelects
                  idPrefix="assistant-model"
                  providers={providers}
                  providerId={providerId}
                  modelId={modelId}
                  reasoningByKind={reasoningByKind}
                  reasoningEffort={reasoningEffort}
                  onProviderChange={(next) => {
                    setProviderId(next);
                    setModelId("");
                    setReasoningEffort("off");
                    saveMutation.reset();
                  }}
                  onModelChange={(next) => {
                    setModelId(next);
                    saveMutation.reset();
                  }}
                  onReasoningChange={(next) => {
                    setReasoningEffort(next);
                    saveMutation.reset();
                  }}
                  disabled={busy}
                />

                {saveMutation.isError ? (
                  <p className="text-destructive text-sm" data-testid="assistant-model-error">
                    {errorText(saveMutation.error)}
                  </p>
                ) : saveMutation.isSuccess ? (
                  <p className="text-sm text-emerald-600" role="status">
                    {t("saved")}
                  </p>
                ) : null}

                <div className="flex justify-end gap-2">
                  {current?.has_tenant_override ? (
                    <Button
                      type="button"
                      variant="outline"
                      data-testid="assistant-model-clear"
                      disabled={busy}
                      onClick={() => clearMutation.mutate()}
                    >
                      {t("backToDefault")}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    data-testid="assistant-model-save"
                    disabled={busy || !providerId || !modelId}
                    onClick={() => saveMutation.mutate()}
                  >
                    {saveMutation.isPending ? t("saving") : t("saveModel")}
                  </Button>
                </div>
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// System Admin — the platform default model (inherited by tenants)
// ---------------------------------------------------------------------------
export function PlatformDefaultModelCard() {
  const t = useT("assistantModel");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState("off");
  const [seeded, setSeeded] = useState(false);

  const optionsQuery = useQuery<AssistantModelOptions, ApiError>({
    queryKey: ["assistant-default-model-options"],
    queryFn: getAssistantDefaultModelOptions,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const currentQuery = useQuery<AssistantDefaultModel, ApiError>({
    queryKey: ["assistant-default-model"],
    queryFn: getAssistantDefaultModel,
    refetchOnWindowFocus: false,
    retry: false,
  });

  useEffect(() => {
    if (seeded || !currentQuery.data) return;
    if (currentQuery.data.provider_id) {
      setProviderId(currentQuery.data.provider_id);
      setModelId(currentQuery.data.model_id ?? "");
      setReasoningEffort(currentQuery.data.reasoning_effort ?? "off");
    }
    setSeeded(true);
  }, [seeded, currentQuery.data]);

  const providers = optionsQuery.data?.providers ?? [];
  const reasoningByKind = optionsQuery.data?.reasoning_by_kind ?? {};

  const saveMutation = useMutation<AssistantDefaultModel, ApiError>({
    mutationFn: () => setAssistantDefaultModel(providerId, modelId, reasoningEffort),
    onSuccess: (data) => queryClient.setQueryData(["assistant-default-model"], data),
  });
  const clearMutation = useMutation<AssistantDefaultModel, ApiError>({
    mutationFn: clearAssistantDefaultModel,
    onSuccess: (data) => {
      queryClient.setQueryData(["assistant-default-model"], data);
      setProviderId("");
      setModelId("");
      setReasoningEffort("off");
      saveMutation.reset();
    },
  });
  const current = currentQuery.data;
  const busy = saveMutation.isPending || clearMutation.isPending;
  const hasDefault = Boolean(current?.provider_id);

  return (
    <Card className="mt-6" data-testid="assistant-default-model-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Globe className="h-5 w-5" />
          {t("platformTitle")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-muted-foreground text-sm">{t("platformDescription")}</p>

        {optionsQuery.isLoading || currentQuery.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            {t("loading")}
          </p>
        ) : (
          <>
            <p
              className="text-muted-foreground text-sm"
              data-testid="assistant-default-model-effective"
            >
              {hasDefault ? (
                <>
                  {t("platformCurrent")}{" "}
                  <strong>
                    {current?.provider_display_name} · {current?.model_id}
                  </strong>
                  {current && !current.is_valid ? (
                    <span className="text-destructive">{t("platformInvalid")}</span>
                  ) : null}
                </>
              ) : (
                t("platformNone")
              )}
            </p>

            {providers.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("noProvidersPlatform")}</p>
            ) : (
              <>
                <ProviderModelSelects
                  idPrefix="assistant-default-model"
                  providers={providers}
                  providerId={providerId}
                  modelId={modelId}
                  reasoningByKind={reasoningByKind}
                  reasoningEffort={reasoningEffort}
                  onProviderChange={(next) => {
                    setProviderId(next);
                    setModelId("");
                    setReasoningEffort("off");
                    saveMutation.reset();
                  }}
                  onModelChange={(next) => {
                    setModelId(next);
                    saveMutation.reset();
                  }}
                  onReasoningChange={(next) => {
                    setReasoningEffort(next);
                    saveMutation.reset();
                  }}
                  disabled={busy}
                />

                {saveMutation.isError ? (
                  <p
                    className="text-destructive text-sm"
                    data-testid="assistant-default-model-error"
                  >
                    {errorText(saveMutation.error)}
                  </p>
                ) : saveMutation.isSuccess ? (
                  <p className="text-sm text-emerald-600" role="status">
                    {t("platformSaved")}
                  </p>
                ) : null}

                <div className="flex justify-end gap-2">
                  {hasDefault ? (
                    <Button
                      type="button"
                      variant="outline"
                      data-testid="assistant-default-model-clear"
                      disabled={busy}
                      onClick={() => clearMutation.mutate()}
                    >
                      {t("clearDefault")}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    data-testid="assistant-default-model-save"
                    disabled={busy || !providerId || !modelId}
                    onClick={() => saveMutation.mutate()}
                  >
                    {saveMutation.isPending ? t("saving") : t("saveDefault")}
                  </Button>
                </div>
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
