"use client";

/**
 * task_12_04 — Programación de backups (System Admin).
 *
 * El backup diario (cadencia cron + ventana/hora + retención local) se
 * configura aquí, desde el panel admin — NO es un cron hardcodeado. Los tres
 * valores viven en `platform_settings` (`backup_enabled` / `backup_cron` /
 * `backup_retention_days`); la beat task `workers.run_daily_backup` los lee en
 * vivo, así que un cambio surte efecto en la siguiente ejecución sin reiniciar
 * Celery.
 *
 * Permisos: LECTURA abierta a cualquier miembro autenticado (la card muestra
 * los valores actuales); ESCRITURA solo System Admin (`RoleGuard
 * min="system_admin"` + el backend gatea con `require_system_admin`). Un
 * cron inválido o una retención fuera de rango -> 422 con el detalle.
 *
 * Endpoints backend (routers/backup.py):
 *   GET /admin/backup/schedule   — lee enabled + cron + retention_days
 *   PUT /admin/backup/schedule   — los persiste (System Admin)
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

interface BackupSchedule {
  enabled: boolean;
  cron: string;
  retention_days: number;
}

const DEFAULT_SCHEDULE: BackupSchedule = {
  enabled: true,
  cron: "0 3 * * *",
  retention_days: 7,
};

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

export default function BackupSchedulePage() {
  const queryClient = useQueryClient();

  const [enabled, setEnabled] = useState(DEFAULT_SCHEDULE.enabled);
  const [cron, setCron] = useState(DEFAULT_SCHEDULE.cron);
  const [retentionDays, setRetentionDays] = useState(String(DEFAULT_SCHEDULE.retention_days));

  const scheduleQuery = useQuery({
    queryKey: ["backup-schedule"],
    queryFn: () => apiFetch<BackupSchedule>("/admin/backup/schedule"),
    refetchOnWindowFocus: false,
  });

  // Seed the form once the GET returns.
  useEffect(() => {
    const data = scheduleQuery.data;
    if (!data) return;
    setEnabled(data.enabled);
    setCron(data.cron);
    setRetentionDays(String(data.retention_days));
  }, [scheduleQuery.data]);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<BackupSchedule>("/admin/backup/schedule", {
        method: "PUT",
        body: {
          enabled,
          cron: cron.trim(),
          retention_days: Number(retentionDays),
        },
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["backup-schedule"], data);
    },
  });

  const data = scheduleQuery.data;
  const isDirty =
    data !== undefined &&
    (enabled !== data.enabled ||
      cron.trim() !== data.cron ||
      Number(retentionDays) !== data.retention_days);

  const retentionNum = Number(retentionDays);
  const retentionValid =
    retentionDays.trim() !== "" &&
    Number.isInteger(retentionNum) &&
    retentionNum >= 1 &&
    retentionNum <= 3650;
  const canSave = cron.trim() !== "" && retentionValid;

  return (
    <div
      className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="backup-schedule-page"
    >
      <PageHeader
        icon={<DatabaseBackup className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Programación de backups"
        description="Cadencia (cron), ventana horaria y retención local del backup diario. Lectura abierta; edición solo System Admin."
        data-testid="backup-schedule-header"
      />

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Configuración</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {scheduleQuery.isLoading ? (
            <p className="text-muted-foreground text-sm" data-testid="backup-schedule-loading">
              Cargando…
            </p>
          ) : scheduleQuery.isError ? (
            <p className="text-destructive text-sm" data-testid="backup-schedule-error">
              {errorText(scheduleQuery.error)}
            </p>
          ) : (
            <RoleGuard
              min="system_admin"
              fallback={
                <ReadOnlySchedule enabled={enabled} cron={cron} retentionDays={retentionDays} />
              }
            >
              <form
                className="space-y-4"
                data-testid="backup-schedule-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (canSave && !mutation.isPending) mutation.mutate();
                }}
              >
                <div className="flex items-center gap-2">
                  <input
                    id="backup-enabled"
                    type="checkbox"
                    className="h-4 w-4"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                    data-testid="backup-enabled-input"
                  />
                  <Label htmlFor="backup-enabled">Backup diario activado</Label>
                </div>

                <div className="space-y-1">
                  <Label htmlFor="backup-cron-input">Cron (ventana horaria)</Label>
                  <Input
                    id="backup-cron-input"
                    data-testid="backup-cron-input"
                    type="text"
                    value={cron}
                    onChange={(e) => setCron(e.target.value)}
                    placeholder="0 3 * * *"
                  />
                  <p className="text-muted-foreground text-xs">
                    5 campos: minuto hora día-del-mes mes día-de-la-semana. Por defecto las 03:00
                    cada día (&quot;0 3 * * *&quot;).
                  </p>
                </div>

                <div className="space-y-1">
                  <Label htmlFor="backup-retention-input">Retención local (días)</Label>
                  <Input
                    id="backup-retention-input"
                    data-testid="backup-retention-input"
                    type="number"
                    min={1}
                    max={3650}
                    step={1}
                    value={retentionDays}
                    onChange={(e) => setRetentionDays(e.target.value)}
                    placeholder="7"
                  />
                  <p className="text-muted-foreground text-xs">
                    Los bundles más antiguos que esta ventana se eliminan tras un backup correcto
                    (entre 1 y 3650 días).
                  </p>
                </div>

                {mutation.isError ? (
                  <p className="text-destructive text-xs" data-testid="backup-schedule-save-error">
                    {errorText(mutation.error)}
                  </p>
                ) : mutation.isSuccess && !isDirty ? (
                  <p className="text-xs text-emerald-600" data-testid="backup-schedule-saved">
                    Guardado.
                  </p>
                ) : null}

                <div className="flex justify-end">
                  <Button
                    type="submit"
                    disabled={!isDirty || !canSave || mutation.isPending}
                    data-testid="backup-schedule-submit"
                  >
                    {mutation.isPending ? "Guardando…" : "Guardar"}
                  </Button>
                </div>
              </form>
            </RoleGuard>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Vista de solo lectura para un miembro no System Admin: ve los valores
 * actuales pero no puede editarlos (el RoleGuard renderiza esto como fallback).
 */
function ReadOnlySchedule({
  enabled,
  cron,
  retentionDays,
}: {
  enabled: boolean;
  cron: string;
  retentionDays: string;
}) {
  return (
    <dl className="space-y-2 text-sm" data-testid="backup-schedule-readonly">
      <div className="flex justify-between">
        <dt className="text-muted-foreground">Estado</dt>
        <dd data-testid="backup-readonly-enabled">{enabled ? "Activado" : "Desactivado"}</dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-muted-foreground">Cron</dt>
        <dd className="font-mono text-xs" data-testid="backup-readonly-cron">
          {cron}
        </dd>
      </div>
      <div className="flex justify-between">
        <dt className="text-muted-foreground">Retención</dt>
        <dd data-testid="backup-readonly-retention">{retentionDays} días</dd>
      </div>
    </dl>
  );
}
