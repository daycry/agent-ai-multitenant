"use client";

/**
 * task_12_12 — Restore desde backup (System Admin).
 *
 * El restore reconstruye el stack (o un único tenant) desde un backup. Es LARGO
 * y DESTRUCTIVO, así que NO se ejecuta en línea: el backend ENCOLA un job Celery
 * en segundo plano (`workers.run_restore` / `workers.run_restore_per_tenant`) y
 * esta página sondea su estado.
 *
 * Flujo:
 *   1. Lista de backups disponibles (local en disco + remotos vía destinos).
 *   2. Preview del bundle elegido (contenido del manifest + opción por-tenant).
 *   3. Elección restore COMPLETO vs SELECTIVO por-tenant.
 *   4. Diálogo de DOBLE CONFIRMACIÓN (el restore es destructivo): el operador
 *      teclea el token de confirmación exacto (el bundle id para un restore
 *      completo; `<tenant_id>@<backup_id>` para uno por-tenant). El backend
 *      re-deriva y valida el token server-side -> 422 si no coincide.
 *   5. Vista de progreso/log que sondea el estado del job hasta SUCCESS/FAILURE.
 *
 * Permisos: RoleGuard min="system_admin" (un miembro normal no ve nada operable).
 * El backend gatea con require_system_admin en las cuatro rutas.
 *
 * Endpoints backend (routers/backup.py):
 *   GET  /admin/backup/restore/backups                       — lista (local+remoto)
 *   GET  /admin/backup/restore/backups/{backup_id}/preview   — manifest + por-tenant
 *   POST /admin/backup/restore                               — encola el job (doble confirm)
 *   GET  /admin/backup/restore/jobs/{job_id}                 — estado pollable
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { DatabaseBackup } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

interface BackupListItem {
  backup_id: string;
  encrypted: boolean | null;
  created_at: string | null;
  total_size_bytes: number | null;
  locations: string[];
}

interface BackupListResponse {
  backups: BackupListItem[];
}

interface BackupArtifactPreview {
  name: string;
  kind: string;
  size_bytes: number;
  source: string | null;
}

interface BackupPreviewResponse {
  backup_id: string;
  encrypted: boolean;
  created_at: string | null;
  status: string | null;
  total_size_bytes: number;
  artifacts: BackupArtifactPreview[];
  per_tenant_available: boolean;
  tenant_scoped_tables: string[];
}

interface RestoreTriggerResponse {
  job_id: string;
  backup_id: string;
  tenant_id: string | null;
  kind: string;
}

interface RestoreJobStatus {
  job_id: string;
  state: string;
  progress: { phase?: string; message?: string } | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

type RestoreKind = "full" | "per_tenant";

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

function formatBytes(n: number | null): string {
  if (n === null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

/** The exact double-confirmation token the backend re-derives + checks. */
function expectedToken(backupId: string, kind: RestoreKind, tenantId: string): string {
  return kind === "per_tenant" ? `${tenantId}@${backupId}` : backupId;
}

const TERMINAL_STATES = new Set(["SUCCESS", "FAILURE"]);

export default function RestorePage() {
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8" data-testid="restore-page">
      <PageHeader
        icon={<DatabaseBackup className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Restaurar desde backup"
        description="Restaura el stack completo o un único tenant desde un backup. Operación larga y destructiva: corre como job en segundo plano y exige doble confirmación. Solo System Admin."
        data-testid="restore-header"
      />

      <RoleGuard
        min="system_admin"
        fallback={
          <Card className="mt-6">
            <CardContent className="py-6">
              <p className="text-muted-foreground text-sm" data-testid="restore-forbidden">
                Solo un System Admin puede restaurar desde un backup.
              </p>
            </CardContent>
          </Card>
        }
      >
        <RestoreWorkspace />
      </RoleGuard>
    </div>
  );
}

function RestoreWorkspace() {
  const [selected, setSelected] = useState<string | null>(null);
  const [kind, setKind] = useState<RestoreKind>("full");
  const [tenantId, setTenantId] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmValue, setConfirmValue] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ["restore-backups"],
    queryFn: () => apiFetch<BackupListResponse>("/admin/backup/restore/backups"),
    refetchOnWindowFocus: false,
  });

  const previewQuery = useQuery({
    queryKey: ["restore-preview", selected],
    queryFn: () =>
      apiFetch<BackupPreviewResponse>(
        `/admin/backup/restore/backups/${encodeURIComponent(selected as string)}/preview`,
      ),
    enabled: selected !== null,
    refetchOnWindowFocus: false,
  });

  // Reset the per-tenant choice when the bundle no longer supports it.
  useEffect(() => {
    if (previewQuery.data && !previewQuery.data.per_tenant_available && kind === "per_tenant") {
      setKind("full");
    }
  }, [previewQuery.data, kind]);

  const triggerMutation = useMutation({
    mutationFn: () =>
      apiFetch<RestoreTriggerResponse>("/admin/backup/restore", {
        method: "POST",
        body: {
          backup_id: selected,
          tenant_id: kind === "per_tenant" ? tenantId.trim() : null,
          confirm: confirmValue,
        },
      }),
    onSuccess: (data) => {
      setJobId(data.job_id);
      setConfirmOpen(false);
      setConfirmValue("");
    },
  });

  // Poll the job status until a terminal state.
  const jobQuery = useQuery({
    queryKey: ["restore-job", jobId],
    queryFn: () =>
      apiFetch<RestoreJobStatus>(
        `/admin/backup/restore/jobs/${encodeURIComponent(jobId as string)}`,
      ),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && TERMINAL_STATES.has(state) ? false : 1500;
    },
  });

  const preview = previewQuery.data;
  const requiredToken =
    selected !== null
      ? expectedToken(selected, kind, kind === "per_tenant" ? tenantId.trim() : "")
      : "";
  const tenantOk = kind === "full" || tenantId.trim() !== "";
  const confirmMatches = confirmValue === requiredToken && requiredToken !== "";

  return (
    <div className="mt-6 space-y-6" data-testid="restore-workspace">
      {/* ----- Backups list ----- */}
      <Card>
        <CardHeader>
          <CardTitle>Backups disponibles</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {listQuery.isLoading ? (
            <p className="text-muted-foreground text-sm" data-testid="restore-list-loading">
              Cargando…
            </p>
          ) : listQuery.isError ? (
            <p className="text-destructive text-sm" data-testid="restore-list-error">
              {errorText(listQuery.error)}
            </p>
          ) : listQuery.data && listQuery.data.backups.length === 0 ? (
            <p className="text-muted-foreground text-sm" data-testid="restore-list-empty">
              No hay backups disponibles.
            </p>
          ) : (
            <ul className="space-y-2" data-testid="restore-list">
              {listQuery.data?.backups.map((b) => (
                <li key={b.backup_id}>
                  <button
                    type="button"
                    data-testid={`restore-backup-${b.backup_id}`}
                    onClick={() => {
                      setSelected(b.backup_id);
                      setKind("full");
                      setTenantId("");
                      setJobId(null);
                    }}
                    className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm ${
                      selected === b.backup_id ? "border-primary bg-accent" : ""
                    }`}
                  >
                    <span className="font-mono text-xs">{b.backup_id}</span>
                    <span className="text-muted-foreground flex items-center gap-3 text-xs">
                      {b.encrypted ? <span>cifrado</span> : null}
                      <span>{formatBytes(b.total_size_bytes)}</span>
                      <span>{b.locations.join(", ")}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ----- Preview pane ----- */}
      {selected !== null ? (
        <Card data-testid="restore-preview-card">
          <CardHeader>
            <CardTitle>Preview del backup</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {previewQuery.isLoading ? (
              <p className="text-muted-foreground text-sm" data-testid="restore-preview-loading">
                Cargando preview…
              </p>
            ) : previewQuery.isError ? (
              <p className="text-destructive text-sm" data-testid="restore-preview-error">
                {errorText(previewQuery.error)}
              </p>
            ) : preview ? (
              <div className="space-y-4" data-testid="restore-preview">
                <dl className="grid grid-cols-2 gap-2 text-sm">
                  <dt className="text-muted-foreground">Backup</dt>
                  <dd className="font-mono text-xs" data-testid="restore-preview-id">
                    {preview.backup_id}
                  </dd>
                  <dt className="text-muted-foreground">Cifrado</dt>
                  <dd data-testid="restore-preview-encrypted">{preview.encrypted ? "Sí" : "No"}</dd>
                  <dt className="text-muted-foreground">Creado</dt>
                  <dd>{preview.created_at ?? "—"}</dd>
                  <dt className="text-muted-foreground">Tamaño total</dt>
                  <dd>{formatBytes(preview.total_size_bytes)}</dd>
                </dl>

                <div>
                  <p className="mb-1 text-sm font-medium">Artefactos</p>
                  <ul className="space-y-1 text-xs" data-testid="restore-preview-artifacts">
                    {preview.artifacts.map((a) => (
                      <li key={a.name} className="flex justify-between rounded border px-2 py-1">
                        <span className="font-mono">{a.name}</span>
                        <span className="text-muted-foreground">
                          {a.kind} · {formatBytes(a.size_bytes)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* ----- Full vs per-tenant choice ----- */}
                <fieldset className="space-y-2" data-testid="restore-kind">
                  <legend className="text-sm font-medium">Tipo de restore</legend>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      name="restore-kind"
                      data-testid="restore-kind-full"
                      checked={kind === "full"}
                      onChange={() => setKind("full")}
                    />
                    Restore completo (detiene el stack y restaura todo)
                  </label>
                  <label
                    className={`flex items-center gap-2 text-sm ${
                      preview.per_tenant_available ? "" : "text-muted-foreground"
                    }`}
                  >
                    <input
                      type="radio"
                      name="restore-kind"
                      data-testid="restore-kind-per-tenant"
                      disabled={!preview.per_tenant_available}
                      checked={kind === "per_tenant"}
                      onChange={() => setKind("per_tenant")}
                    />
                    Restore selectivo por tenant (solo sus datos)
                  </label>

                  {kind === "per_tenant" ? (
                    <div className="space-y-2 pl-6">
                      <div className="space-y-1">
                        <Label htmlFor="restore-tenant-id">Tenant ID (UUID)</Label>
                        <Input
                          id="restore-tenant-id"
                          data-testid="restore-tenant-id"
                          type="text"
                          value={tenantId}
                          onChange={(e) => setTenantId(e.target.value)}
                          placeholder="11111111-0000-0000-0000-000000000001"
                        />
                      </div>
                      <div data-testid="restore-tenant-tables">
                        <p className="text-muted-foreground text-xs">
                          Tablas afectadas (solo las filas de este tenant):
                        </p>
                        <p className="font-mono text-xs">
                          {preview.tenant_scoped_tables.join(", ")}
                        </p>
                      </div>
                    </div>
                  ) : null}
                </fieldset>

                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="destructive"
                    data-testid="restore-open-confirm"
                    disabled={!tenantOk}
                    onClick={() => {
                      setConfirmValue("");
                      triggerMutation.reset();
                      setConfirmOpen(true);
                    }}
                  >
                    Restaurar…
                  </Button>
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {/* ----- Progress / log view ----- */}
      {jobId !== null ? (
        <Card data-testid="restore-progress-card">
          <CardHeader>
            <CardTitle>Progreso del restore</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-sm" data-testid="restore-job-state">
              Estado: <span className="font-mono">{jobQuery.data?.state ?? "PENDING"}</span>
            </p>
            {jobQuery.data?.progress?.message ? (
              <p className="text-muted-foreground text-sm" data-testid="restore-job-message">
                {jobQuery.data.progress.message}
              </p>
            ) : null}
            {jobQuery.data?.state === "SUCCESS" ? (
              <p className="text-sm text-emerald-600" data-testid="restore-job-success">
                Restore completado.
              </p>
            ) : null}
            {jobQuery.data?.state === "FAILURE" ? (
              <p className="text-destructive text-sm" data-testid="restore-job-failure">
                {jobQuery.data.error ?? "El restore falló."}
              </p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {/* ----- Double-confirmation dialog ----- */}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen} size="md">
        <DialogContent data-testid="restore-confirm-dialog">
          <DialogHeader>
            <DialogTitle>Confirmar restore destructivo</DialogTitle>
            <DialogDescription>
              {kind === "per_tenant"
                ? "Vas a sobrescribir SOLO los datos de este tenant con los del backup. El resto de tenants no se ven afectados."
                : "Vas a DETENER el stack y reemplazar la base de datos y los volúmenes con los del backup. Esta acción es destructiva."}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm">
              Para confirmar, teclea exactamente:{" "}
              <code
                className="bg-muted rounded px-1 py-0.5 font-mono text-xs"
                data-testid="restore-confirm-token"
              >
                {requiredToken}
              </code>
            </p>
            <Input
              data-testid="restore-confirm-input"
              type="text"
              value={confirmValue}
              onChange={(e) => setConfirmValue(e.target.value)}
              placeholder={requiredToken}
              autoComplete="off"
            />
            {triggerMutation.isError ? (
              <p className="text-destructive text-xs" data-testid="restore-confirm-error">
                {errorText(triggerMutation.error)}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              data-testid="restore-confirm-cancel"
              onClick={() => setConfirmOpen(false)}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              variant="destructive"
              data-testid="restore-confirm-submit"
              disabled={!confirmMatches || triggerMutation.isPending}
              onClick={() => triggerMutation.mutate()}
            >
              {triggerMutation.isPending ? "Encolando…" : "Confirmar y restaurar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
