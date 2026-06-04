"use client";

/**
 * Memorias settings (Plan 06.7 task_06_7_07).
 *
 * Formulario para `memories.similarity.threshold` (slider) y
 * `memories.similarity.limit` (number). Lee:
 *
 *   GET /tenant-settings/memories
 *
 * y persiste con:
 *
 *   PUT /tenant-settings/memories/similarity.threshold
 *   PUT /tenant-settings/memories/similarity.limit
 *
 * Sin hardcodear nada del registry — los rangos vienen del propio
 * endpoint /_registry.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang } from "@/lib/lang-context";
import { memoryDetectorState } from "@/lib/memory/honesty";

interface RegistrySettingDef {
  type: "float" | "int" | "string" | "bool";
  default: number;
  label_es: string;
  description_es: string;
  min_value: number | null;
  max_value: number | null;
}

interface RegistryCategoryDef {
  label_es: string;
  icon: string;
  description_es: string;
  external_page: string | null;
  settings: Record<string, RegistrySettingDef>;
}

interface SettingValueResponse {
  category: string;
  key: string;
  value: number;
  is_default: boolean;
}

// Forma mínima de `GET /memories` que esta pantalla necesita: solo el flag
// `has_embedding` (memories.py:118) para decidir si el detector puede operar.
interface MemoryEmbeddingProbe {
  has_embedding: boolean;
}

const CATEGORY = "memories";

export default function MemoriesSettingsPage() {
  const queryClient = useQueryClient();
  const { lang } = useLang();

  const registryQuery = useQuery<{ categories: Record<string, RegistryCategoryDef> }, ApiError>({
    queryKey: ["tenant-settings", "_registry"],
    queryFn: () => apiFetch("/tenant-settings/_registry"),
    refetchOnWindowFocus: false,
  });

  const valuesQuery = useQuery<SettingValueResponse[], ApiError>({
    queryKey: ["tenant-settings", CATEGORY],
    queryFn: () => apiFetch<SettingValueResponse[]>(`/tenant-settings/${CATEGORY}`),
    refetchOnWindowFocus: false,
  });

  // Honestidad de estado (Plan 06.17 task_06_17_06): el slider de umbral y el
  // detector de similares solo sirven si ALGUNA memoria tiene embedding. Si no,
  // los controles se marcan "No disponible aún" en vez de fingir que filtran.
  const embeddingProbeQuery = useQuery<MemoryEmbeddingProbe[], ApiError>({
    queryKey: ["memories", "embedding-probe"],
    queryFn: () => apiFetch<MemoryEmbeddingProbe[]>("/memories?limit=200"),
    refetchOnWindowFocus: false,
  });
  const hasAnyEmbedding = (embeddingProbeQuery.data ?? []).some((m) => m.has_embedding);
  const detector = memoryDetectorState(hasAnyEmbedding, lang);
  // Mientras la sonda carga no afirmamos nada (ni activo ni roto).
  const detectorUnavailable = embeddingProbeQuery.isSuccess && !detector.available;

  const [threshold, setThreshold] = useState<number>(0.85);
  const [limit, setLimit] = useState<number>(5);
  const [status, setStatus] = useState<string>("");

  // Hydrate form state from the server once both queries land.
  useEffect(() => {
    if (!valuesQuery.data) return;
    for (const v of valuesQuery.data) {
      if (v.key === "similarity.threshold") setThreshold(Number(v.value));
      if (v.key === "similarity.limit") setLimit(Number(v.value));
    }
  }, [valuesQuery.data]);

  const memoriesDef = registryQuery.data?.categories?.memories;
  const thresholdDef = memoriesDef?.settings["similarity.threshold"];
  const limitDef = memoriesDef?.settings["similarity.limit"];

  const putSetting = useMutation<SettingValueResponse, ApiError, { key: string; value: number }>({
    mutationFn: ({ key, value }) =>
      apiFetch<SettingValueResponse>(`/tenant-settings/${CATEGORY}/${key}`, {
        method: "PUT",
        body: { value },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tenant-settings", CATEGORY] });
    },
  });

  const onSave = async () => {
    setStatus("Guardando…");
    try {
      await Promise.all([
        putSetting.mutateAsync({ key: "similarity.threshold", value: threshold }),
        putSetting.mutateAsync({ key: "similarity.limit", value: limit }),
      ]);
      setStatus("Guardado");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error al guardar";
      setStatus(`Error: ${msg}`);
    }
  };

  return (
    <div
      className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="settings-memories-page"
    >
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Memorias"
        description="Cómo el sistema detecta memorias similares para que el operador las fusione o descarte."
      />

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Detector de similares</CardTitle>
          {detectorUnavailable && (
            <Badge variant="muted" data-testid="settings-memories-unavailable">
              {detector.label}
            </Badge>
          )}
        </CardHeader>
        <CardContent className="space-y-6">
          {detectorUnavailable && (
            <p
              className="bg-muted/40 text-muted-foreground rounded p-3 text-xs"
              data-testid="settings-memories-unavailable-note"
              role="status"
            >
              {detector.note}
            </p>
          )}
          <div className="space-y-2">
            <Label htmlFor="threshold" className="text-sm font-medium">
              {thresholdDef?.label_es ?? "Umbral de similitud"} · {threshold.toFixed(2)}
            </Label>
            <input
              id="threshold"
              type="range"
              min={thresholdDef?.min_value ?? 0.5}
              max={thresholdDef?.max_value ?? 0.99}
              step={0.01}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              data-testid="settings-memories-threshold"
              disabled={detectorUnavailable}
              aria-disabled={detectorUnavailable}
              className="w-full disabled:cursor-not-allowed disabled:opacity-50"
            />
            <p className="text-muted-foreground text-xs">{thresholdDef?.description_es}</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="limit" className="text-sm font-medium">
              {limitDef?.label_es ?? "Número de candidatos"}
            </Label>
            <Input
              id="limit"
              type="number"
              min={limitDef?.min_value ?? 1}
              max={limitDef?.max_value ?? 20}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              data-testid="settings-memories-limit"
              disabled={detectorUnavailable}
              className="w-32"
            />
            <p className="text-muted-foreground text-xs">{limitDef?.description_es}</p>
          </div>

          <div className="flex items-center gap-3">
            <Button
              onClick={() => void onSave()}
              disabled={putSetting.isPending || detectorUnavailable}
              data-testid="settings-memories-save"
            >
              {putSetting.isPending ? "Guardando…" : "Guardar"}
            </Button>
            {status && (
              <span
                className={
                  status.startsWith("Error")
                    ? "text-danger-soft-foreground"
                    : "text-muted-foreground"
                }
                data-testid="settings-memories-status"
              >
                {status}
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
