"use client";

/**
 * Modelo del córtex (cortex.default_model) — selector del System Owner.
 *
 * El córtex es la mente del dueño del despliegue (ADR 0074): un singleton cuyo
 * modelo NO se hereda por tenant — sale solo de un platform-default propio. Hasta
 * ahora `cortex.default_model` no existía en `platform_settings` y el córtex
 * devolvía 503; esta sección le da al owner un selector en el panel (sin SQL),
 * espejo del control del modelo por defecto del asistente:
 *
 *   GET  /owner/cortex/model-options  → proveedores activos + modelos + razonamiento
 *   GET  /owner/cortex/model          → la selección actual (o sin configurar)
 *   PUT  /owner/cortex/model          → fija/limpia (validado contra ADR 0021)
 *
 * Solo visible para el System Owner (la barrera real es `require_system_owner` en
 * el backend; esto es solo UX). El proveedor se elige por NOMBRE (no por kind),
 * igual que el resto de selectores de modelo de la plataforma.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { reasoningLabel as labelFor, selectableReasoningOptions } from "@/lib/model-selection";
import { useErrorText } from "@/lib/use-error-text";
import {
  clearCortexModel,
  getCortexModel,
  getCortexModelOptions,
  setCortexModel,
  type CortexModel,
  type CortexModelOption,
  type CortexModelOptions,
} from "@/lib/cortex";

function modelsFor(providers: CortexModelOption[], providerId: string): string[] {
  return providers.find((p) => p.provider_id === providerId)?.models ?? [];
}

export function CortexModelSection() {
  const t = useT("cortexModel");
  const tCommon = useT("common");
  // `labelFor` trae "Desactivado" como valor por defecto de su parámetro: el
  // llamante le pasa la traducción para que el helper siga sin saber de idiomas
  // (mismo trato que en `components/capability/provider-model-selects.tsx`).
  const reasoningLabel = (opt: string) => labelFor(opt, t("reasoningOff"));
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [providerId, setProviderId] = useState("");
  const [modelId, setModelId] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState("off");
  const [seeded, setSeeded] = useState(false);

  const optionsQuery = useQuery<CortexModelOptions, ApiError>({
    queryKey: ["cortex-model-options"],
    queryFn: getCortexModelOptions,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const currentQuery = useQuery<CortexModel, ApiError>({
    queryKey: ["cortex-model"],
    queryFn: getCortexModel,
    refetchOnWindowFocus: false,
    retry: false,
  });

  // Seed the selects from the stored selection (only when one exists).
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
  const models = providerId ? modelsFor(providers, providerId) : [];
  const kind = providers.find((p) => p.provider_id === providerId)?.kind;
  const reasoningOptions = kind ? (reasoningByKind[kind] ?? []) : [];
  const reasoningSelectable = selectableReasoningOptions(reasoningEffort, reasoningOptions);

  const saveMutation = useMutation<CortexModel, ApiError>({
    mutationFn: () => setCortexModel(providerId, modelId, reasoningEffort),
    onSuccess: (data) => queryClient.setQueryData(["cortex-model"], data),
  });
  const clearMutation = useMutation<CortexModel, ApiError>({
    mutationFn: clearCortexModel,
    onSuccess: (data) => {
      queryClient.setQueryData(["cortex-model"], data);
      setProviderId("");
      setModelId("");
      setReasoningEffort("off");
      saveMutation.reset();
    },
  });

  const current = currentQuery.data;
  const busy = saveMutation.isPending || clearMutation.isPending;
  const hasModel = Boolean(current?.provider_id);

  return (
    <Card className="mt-6" data-testid="cortex-model-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Brain className="h-5 w-5" />
          {t("title")}
        </CardTitle>
        <p className="text-muted-foreground text-sm">{t("description")}</p>
      </CardHeader>
      <CardContent className="space-y-4" data-testid="cortex-model-control">
        {optionsQuery.isLoading || currentQuery.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            {tCommon("loading")}
          </p>
        ) : (
          <>
            <p className="text-muted-foreground text-sm" data-testid="cortex-model-effective">
              {hasModel ? (
                <>
                  {t("currentModel")}{" "}
                  <strong>
                    {current?.provider_display_name} · {current?.model_id}
                  </strong>
                  {current && !current.is_valid ? (
                    <span className="text-destructive"> {t("invalid")}</span>
                  ) : null}
                </>
              ) : (
                t("unset")
              )}
            </p>

            {providers.length === 0 ? (
              <p className="text-muted-foreground text-sm">{t("noProviders")}</p>
            ) : (
              <>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="cortex-model-provider">{t("provider")}</Label>
                    <Select
                      id="cortex-model-provider"
                      data-testid="cortex-model-provider"
                      value={providerId}
                      disabled={busy}
                      onChange={(e) => {
                        setProviderId(e.target.value);
                        setModelId("");
                        setReasoningEffort("off");
                        saveMutation.reset();
                      }}
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
                    <Label htmlFor="cortex-model-model">{t("model")}</Label>
                    <Select
                      id="cortex-model-model"
                      data-testid="cortex-model-model"
                      value={modelId}
                      disabled={busy || !providerId || models.length === 0}
                      onChange={(e) => {
                        setModelId(e.target.value);
                        saveMutation.reset();
                      }}
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
                      <Label htmlFor="cortex-model-reasoning">{t("reasoning")}</Label>
                      <Select
                        id="cortex-model-reasoning"
                        data-testid="cortex-model-reasoning"
                        value={reasoningEffort}
                        disabled={busy || !providerId}
                        onChange={(e) => {
                          setReasoningEffort(e.target.value);
                          saveMutation.reset();
                        }}
                      >
                        {reasoningSelectable.map((o) => (
                          <option key={o} value={o}>
                            {reasoningLabel(o)}
                          </option>
                        ))}
                      </Select>
                    </div>
                  ) : null}
                </div>

                {saveMutation.isError ? (
                  <p className="text-destructive text-sm" data-testid="cortex-model-error">
                    {errorText(saveMutation.error)}
                  </p>
                ) : saveMutation.isSuccess ? (
                  <p className="text-sm text-emerald-600" role="status">
                    {t("savedOk")}
                  </p>
                ) : null}

                <div className="flex justify-end gap-2">
                  {hasModel ? (
                    <Button
                      type="button"
                      variant="outline"
                      data-testid="cortex-model-clear"
                      disabled={busy}
                      onClick={() => clearMutation.mutate()}
                    >
                      {t("clear")}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    data-testid="cortex-model-save"
                    disabled={busy || !providerId || !modelId}
                    onClick={() => saveMutation.mutate()}
                  >
                    {saveMutation.isPending ? t("saving") : t("save")}
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
