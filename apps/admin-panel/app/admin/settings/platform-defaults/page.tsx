"use client";

/**
 * Valores por defecto de plataforma (System Admin).
 *
 * Edita los `platform_settings` operator-tunables que NO tenían UI (sobre todo
 * `model.default_config` — el modelo por defecto de agentes, ADR 0055). Guiada
 * por el registro del backend, sin hardcodear nada:
 *
 *   GET  /admin/platform-settings/_registry   → grupos + tipos + límites
 *   GET  /admin/platform-settings             → valores actuales
 *   PUT  /admin/platform-settings/{key}        → valida (por tipo) + persiste
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Settings2, ShieldAlert } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types — mirror api_server.platform_settings_registry.platform_registry_to_dict.
// ---------------------------------------------------------------------------
type SettingType = "bool" | "int" | "decimal" | "model_config";

interface SettingDef {
  type: SettingType;
  default: unknown;
  label_es: string;
  description_es: string;
  min_value: number | null;
  max_value: number | null;
  provider_kinds?: string[];
}

interface CategoryDef {
  label_es: string;
  icon: string;
  description_es: string;
  settings: Record<string, SettingDef>;
}

interface SettingValue {
  key: string;
  value: unknown;
  is_default: boolean;
}

interface ModelConfig {
  provider?: string;
  model?: string;
  temperature?: number;
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.body || err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function PlatformDefaultsPage() {
  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="platform-defaults-page"
    >
      <PageHeader
        icon={<Settings2 className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Valores por defecto de plataforma"
        description="Ajustes globales de la plataforma sin página propia (modelo por defecto de agentes, límites de ejecución, RAG, mantenimiento…). Solo System Admin."
        data-testid="platform-defaults-header"
      />
      <RoleGuard
        min="system_admin"
        fallback={
          <Card className="mt-6">
            <CardContent className="flex items-center gap-3 py-10">
              <ShieldAlert className="text-muted-foreground h-5 w-5 shrink-0" />
              <p className="text-muted-foreground text-sm">
                Esta sección es exclusiva del System Admin de la plataforma.
              </p>
            </CardContent>
          </Card>
        }
      >
        <PlatformDefaultsContent />
      </RoleGuard>
    </div>
  );
}

function PlatformDefaultsContent() {
  const registryQuery = useQuery<{ categories: Record<string, CategoryDef> }, ApiError>({
    queryKey: ["platform-settings", "_registry"],
    queryFn: () => apiFetch("/admin/platform-settings/_registry"),
    refetchOnWindowFocus: false,
  });
  const valuesQuery = useQuery<SettingValue[], ApiError>({
    queryKey: ["platform-settings", "values"],
    queryFn: () => apiFetch<SettingValue[]>("/admin/platform-settings"),
    refetchOnWindowFocus: false,
  });

  const valueByKey = new Map((valuesQuery.data ?? []).map((v) => [v.key, v]));

  return (
    <div className="mt-6">
      <StateBlock
        isLoading={registryQuery.isLoading || valuesQuery.isLoading}
        isError={registryQuery.isError || valuesQuery.isError}
        error={registryQuery.error ?? valuesQuery.error}
        loadingLabel="Cargando ajustes…"
      >
        <div className="space-y-6">
          {Object.entries(registryQuery.data?.categories ?? {}).map(([catKey, cat]) => (
            <Card key={catKey} data-testid={`platform-cat-${catKey}`}>
              <CardHeader>
                <CardTitle className="text-base">{cat.label_es}</CardTitle>
                {cat.description_es ? (
                  <p className="text-muted-foreground text-sm">{cat.description_es}</p>
                ) : null}
              </CardHeader>
              <CardContent className="space-y-6">
                {Object.entries(cat.settings).map(([key, def]) => (
                  <SettingControl
                    key={key}
                    settingKey={key}
                    def={def}
                    current={valueByKey.get(key)}
                  />
                ))}
              </CardContent>
            </Card>
          ))}
        </div>
      </StateBlock>
    </div>
  );
}

function SettingControl({
  settingKey,
  def,
  current,
}: {
  settingKey: string;
  def: SettingDef;
  current: SettingValue | undefined;
}) {
  const queryClient = useQueryClient();
  const initial = current ? current.value : def.default;
  const [value, setValue] = useState<unknown>(initial);
  const [msg, setMsg] = useState<string | null>(null);

  const save = useMutation<SettingValue, ApiError, unknown>({
    mutationFn: (v) =>
      apiFetch<SettingValue>(`/admin/platform-settings/${settingKey}`, {
        method: "PUT",
        body: { value: v },
      }),
    onSuccess: () => {
      setMsg("Guardado ✓");
      void queryClient.invalidateQueries({ queryKey: ["platform-settings", "values"] });
    },
    onError: (err) => setMsg(errorText(err)),
  });

  return (
    <div className="border-border/60 flex flex-col gap-2 border-t pt-4 first:border-t-0 first:pt-0">
      <div className="flex flex-col gap-0.5">
        <Label className="text-sm font-medium">{def.label_es}</Label>
        <p className="text-muted-foreground text-xs">{def.description_es}</p>
        <code className="text-muted-foreground text-[11px]">{settingKey}</code>
      </div>

      {def.type === "bool" ? (
        <div className="flex items-center justify-between">
          <label htmlFor={settingKey} className="flex items-center gap-2 text-sm">
            <Checkbox
              id={settingKey}
              checked={Boolean(value)}
              onChange={(e) => setValue(e.target.checked)}
            />
            {value ? "Activado" : "Desactivado"}
          </label>
          <SaveButton onClick={() => save.mutate(value)} pending={save.isPending} />
        </div>
      ) : def.type === "int" ? (
        <div className="flex items-end gap-2">
          <Input
            type="number"
            value={String(value ?? "")}
            min={def.min_value ?? undefined}
            max={def.max_value ?? undefined}
            onChange={(e) => setValue(Number(e.target.value))}
            className="max-w-[12rem]"
          />
          <SaveButton onClick={() => save.mutate(value)} pending={save.isPending} />
        </div>
      ) : def.type === "decimal" ? (
        <div className="flex items-end gap-2">
          <Input
            value={String(value ?? "")}
            onChange={(e) => setValue(e.target.value)}
            placeholder="0"
            className="max-w-[12rem]"
          />
          <SaveButton onClick={() => save.mutate(value)} pending={save.isPending} />
        </div>
      ) : def.type === "model_config" ? (
        <ModelConfigControl
          value={(value ?? {}) as ModelConfig}
          providerKinds={def.provider_kinds ?? []}
          onChange={setValue}
          onSave={() => save.mutate(value)}
          pending={save.isPending}
        />
      ) : null}

      {msg ? <p className="text-muted-foreground text-xs">{msg}</p> : null}
    </div>
  );
}

function ModelConfigControl({
  value,
  providerKinds,
  onChange,
  onSave,
  pending,
}: {
  value: ModelConfig;
  providerKinds: string[];
  onChange: (v: ModelConfig) => void;
  onSave: () => void;
  pending: boolean;
}) {
  const set = (patch: Partial<ModelConfig>) => onChange({ ...value, ...patch });
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div className="space-y-1">
        <Label className="text-xs">Proveedor (kind)</Label>
        <Select value={value.provider ?? ""} onChange={(e) => set({ provider: e.target.value })}>
          <option value="">—</option>
          {providerKinds.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Modelo</Label>
        <Input
          value={value.model ?? ""}
          onChange={(e) => set({ model: e.target.value })}
          placeholder="qwen3-coder:480b"
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Temperatura</Label>
        <div className="flex items-end gap-2">
          <Input
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={value.temperature ?? 0.2}
            onChange={(e) => set({ temperature: Number(e.target.value) })}
          />
          <SaveButton onClick={onSave} pending={pending} />
        </div>
      </div>
    </div>
  );
}

function SaveButton({ onClick, pending }: { onClick: () => void; pending: boolean }) {
  return (
    <Button size="sm" onClick={onClick} disabled={pending} data-testid="platform-setting-save">
      {pending ? "Guardando…" : "Guardar"}
    </Button>
  );
}
