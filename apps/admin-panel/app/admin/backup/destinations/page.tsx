"use client";

/**
 * task_12_09 — Destinos remotos de backup (System Admin).
 *
 * Tras un backup correcto y verificado, el bundle se sube a cada destino remoto
 * configurado + habilitado (S3, B2, SFTP/NAS, rclone). Esta página gestiona la
 * LISTA de destinos (añadir / editar / habilitar) y ofrece un botón "Probar
 * conexión" por destino.
 *
 * Secretos: las CREDENCIALES (access key/secret de S3, keyId/key de B2,
 * password/clave privada de SFTP, el blob de config de rclone) NUNCA se
 * configuran ni se muestran aquí — viven en el secret seam de los workers
 * (Vault/env). Esta UI sólo maneja la config NO secreta (bucket, endpoint, host,
 * path, remote). El backend rechaza con 422 cualquier campo fuera de la lista
 * blanca no secreta, así que un secreto nunca llega a persistirse ni a devolverse.
 *
 * Permisos: RoleGuard min="system_admin" (un miembro normal ve una vista de solo
 * lectura). El backend gatea con require_system_admin.
 *
 * Endpoints backend (routers/backup.py):
 *   GET  /admin/backup/destinations              — lee la lista (config no secreta)
 *   PUT  /admin/backup/destinations              — la persiste (System Admin)
 *   POST /admin/backup/destinations/{name}/test  — prueba conectividad de un destino
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseBackup, Plus, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT, type MessageKey, type Translator } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

type DestinationType = "s3" | "b2" | "sftp" | "rclone";

interface Destination {
  type: DestinationType;
  name: string;
  enabled: boolean;
  config: Record<string, string>;
}

interface DestinationsResponse {
  destinations: Destination[];
}

interface ConnectivityResult {
  ok: boolean;
  detail: string;
}

/**
 * Campos NO secretos por tipo (los que la UI edita). Nunca credenciales.
 *
 * El label NO es un literal: es la clave del diccionario (`labelKey`). Antes
 * estaba cableado en castellano y con el toggle en EN seguía diciendo "Prefijo"
 * o "Ruta remota" — el hallazgo frontend-9 en un sitio donde no se ve a simple
 * vista, porque la tabla vive fuera del JSX.
 */
const TYPE_FIELDS: Record<
  DestinationType,
  { key: string; labelKey: MessageKey<"backupDestinations">; required: boolean }[]
> = {
  s3: [
    { key: "bucket", labelKey: "fieldBucket", required: true },
    { key: "prefix", labelKey: "fieldPrefix", required: false },
    { key: "endpoint_url", labelKey: "fieldEndpointUrl", required: false },
    { key: "region", labelKey: "fieldRegion", required: false },
  ],
  b2: [
    { key: "bucket", labelKey: "fieldBucket", required: true },
    { key: "region", labelKey: "fieldRegionB2", required: true },
    { key: "prefix", labelKey: "fieldPrefix", required: false },
  ],
  sftp: [
    { key: "host", labelKey: "fieldHost", required: true },
    { key: "username", labelKey: "fieldUsername", required: true },
    { key: "port", labelKey: "fieldPort", required: false },
    { key: "remote_path", labelKey: "fieldRemotePath", required: false },
    { key: "host_key_policy", labelKey: "fieldHostKeyPolicy", required: false },
  ],
  rclone: [
    { key: "remote", labelKey: "fieldRemote", required: true },
    { key: "path", labelKey: "fieldPath", required: false },
  ],
};

/** Orden estable del desplegable de tipo, con su clave de traducción. */
const TYPE_KEYS: Record<DestinationType, MessageKey<"backupDestinations">> = {
  s3: "typeS3",
  b2: "typeB2",
  sftp: "typeSftp",
  rclone: "typeRclone",
};

function emptyDestination(): Destination {
  return { type: "s3", name: "", enabled: true, config: {} };
}

/** Cliente: ¿están los campos requeridos del tipo presentes? */
function isComplete(dest: Destination): boolean {
  if (dest.name.trim() === "") return false;
  return TYPE_FIELDS[dest.type].every(
    (f) => !f.required || (dest.config[f.key] ?? "").trim() !== "",
  );
}

export default function BackupDestinationsPage() {
  const t = useT("backupDestinations");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [testResults, setTestResults] = useState<Record<string, ConnectivityResult | "pending">>(
    {},
  );

  const query = useQuery({
    queryKey: ["backup-destinations"],
    queryFn: () => apiFetch<DestinationsResponse>("/admin/backup/destinations"),
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (query.data) setDestinations(query.data.destinations);
  }, [query.data]);

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<DestinationsResponse>("/admin/backup/destinations", {
        method: "PUT",
        body: {
          destinations: destinations.map((d) => ({
            type: d.type,
            name: d.name.trim(),
            enabled: d.enabled,
            config: d.config,
          })),
        },
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["backup-destinations"], data);
      setDestinations(data.destinations);
    },
  });

  function updateAt(idx: number, patch: Partial<Destination>): void {
    setDestinations((prev) => prev.map((d, i) => (i === idx ? { ...d, ...patch } : d)));
  }

  function updateConfigAt(idx: number, key: string, value: string): void {
    setDestinations((prev) =>
      prev.map((d, i) => (i === idx ? { ...d, config: { ...d.config, [key]: value } } : d)),
    );
  }

  function removeAt(idx: number): void {
    setDestinations((prev) => prev.filter((_, i) => i !== idx));
  }

  async function testConnection(name: string): Promise<void> {
    setTestResults((prev) => ({ ...prev, [name]: "pending" }));
    try {
      const result = await apiFetch<ConnectivityResult>(
        `/admin/backup/destinations/${encodeURIComponent(name)}/test`,
        { method: "POST" },
      );
      setTestResults((prev) => ({ ...prev, [name]: result }));
    } catch (err) {
      setTestResults((prev) => ({ ...prev, [name]: { ok: false, detail: errorText(err) } }));
    }
  }

  const allComplete = destinations.every(isComplete);

  return (
    <div
      className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="backup-destinations-page"
    >
      <PageHeader
        icon={<DatabaseBackup className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="backup-destinations-header"
      />

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>{t("cardTitle")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {query.isLoading ? (
            <p className="text-muted-foreground text-sm" data-testid="backup-destinations-loading">
              {t("loading")}
            </p>
          ) : query.isError ? (
            <p className="text-destructive text-sm" data-testid="backup-destinations-error">
              {errorText(query.error)}
            </p>
          ) : (
            <RoleGuard
              min="system_admin"
              fallback={<ReadOnlyDestinations destinations={destinations} />}
            >
              <div className="space-y-4" data-testid="backup-destinations-editor">
                {destinations.length === 0 ? (
                  <p
                    className="text-muted-foreground text-sm"
                    data-testid="backup-destinations-empty"
                  >
                    {t("empty")}
                  </p>
                ) : (
                  destinations.map((dest, idx) => (
                    <DestinationCard
                      key={idx}
                      t={t}
                      dest={dest}
                      index={idx}
                      testResult={testResults[dest.name]}
                      onChange={(patch) => updateAt(idx, patch)}
                      onConfigChange={(key, value) => updateConfigAt(idx, key, value)}
                      onRemove={() => removeAt(idx)}
                      onTest={() => testConnection(dest.name)}
                    />
                  ))
                )}

                <div className="flex items-center justify-between">
                  <Button
                    type="button"
                    variant="outline"
                    data-testid="backup-destination-add"
                    onClick={() => setDestinations((prev) => [...prev, emptyDestination()])}
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    {t("add")}
                  </Button>

                  <div className="flex items-center gap-3">
                    {saveMutation.isError ? (
                      <p
                        className="text-destructive text-xs"
                        data-testid="backup-destinations-save-error"
                      >
                        {errorText(saveMutation.error)}
                      </p>
                    ) : saveMutation.isSuccess ? (
                      <p
                        className="text-xs text-emerald-600"
                        data-testid="backup-destinations-saved"
                      >
                        {t("saved")}
                      </p>
                    ) : null}
                    <Button
                      type="button"
                      data-testid="backup-destinations-submit"
                      disabled={!allComplete || saveMutation.isPending}
                      onClick={() => saveMutation.mutate()}
                    >
                      {saveMutation.isPending ? t("saving") : t("save")}
                    </Button>
                  </div>
                </div>
              </div>
            </RoleGuard>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function DestinationCard({
  t,
  dest,
  index,
  testResult,
  onChange,
  onConfigChange,
  onRemove,
  onTest,
}: {
  t: Translator<"backupDestinations">;
  dest: Destination;
  index: number;
  testResult: ConnectivityResult | "pending" | undefined;
  onChange: (patch: Partial<Destination>) => void;
  onConfigChange: (key: string, value: string) => void;
  onRemove: () => void;
  onTest: () => void;
}) {
  const fields = TYPE_FIELDS[dest.type];
  return (
    <div
      className="space-y-3 rounded-md border p-4"
      data-testid={`backup-destination-card-${index}`}
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor={`dest-type-${index}`}>{t("typeLabel")}</Label>
          <select
            id={`dest-type-${index}`}
            data-testid={`backup-destination-type-${index}`}
            className="border-input bg-background ring-offset-background h-10 rounded-md border px-3 text-sm"
            value={dest.type}
            onChange={(e) => onChange({ type: e.target.value as DestinationType, config: {} })}
          >
            {(Object.keys(TYPE_KEYS) as DestinationType[]).map((type) => (
              <option key={type} value={type}>
                {t(TYPE_KEYS[type])}
              </option>
            ))}
          </select>
        </div>

        <div className="flex-1 space-y-1">
          <Label htmlFor={`dest-name-${index}`}>{t("nameLabel")}</Label>
          <Input
            id={`dest-name-${index}`}
            data-testid={`backup-destination-name-${index}`}
            type="text"
            value={dest.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="offsite-s3"
          />
        </div>

        <div className="flex items-center gap-2 pb-2">
          <input
            id={`dest-enabled-${index}`}
            type="checkbox"
            className="h-4 w-4"
            checked={dest.enabled}
            onChange={(e) => onChange({ enabled: e.target.checked })}
            data-testid={`backup-destination-enabled-${index}`}
          />
          <Label htmlFor={`dest-enabled-${index}`}>{t("enabledLabel")}</Label>
        </div>

        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={t("remove")}
          data-testid={`backup-destination-remove-${index}`}
          onClick={onRemove}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {fields.map((f) => (
          <div key={f.key} className="space-y-1">
            <Label htmlFor={`dest-${index}-${f.key}`}>
              {t(f.labelKey)}
              {f.required ? " *" : ""}
            </Label>
            <Input
              id={`dest-${index}-${f.key}`}
              data-testid={`backup-destination-${index}-${f.key}`}
              type="text"
              value={dest.config[f.key] ?? ""}
              onChange={(e) => onConfigChange(f.key, e.target.value)}
            />
          </div>
        ))}
      </div>

      <p className="text-muted-foreground text-xs">{t("credentialsNote")}</p>

      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid={`backup-destination-test-${index}`}
          onClick={onTest}
        >
          {t("test")}
        </Button>
        {testResult === "pending" ? (
          <span
            className="text-muted-foreground text-xs"
            data-testid={`backup-destination-test-pending-${index}`}
          >
            {t("testing")}
          </span>
        ) : testResult ? (
          <span
            className={testResult.ok ? "text-xs text-emerald-600" : "text-destructive text-xs"}
            data-testid={`backup-destination-test-result-${index}`}
          >
            {testResult.ok ? t("testOk") : t("testFail")}: {testResult.detail}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** Vista de solo lectura para un miembro no System Admin. */
function ReadOnlyDestinations({ destinations }: { destinations: Destination[] }) {
  const t = useT("backupDestinations");
  if (destinations.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="backup-destinations-readonly-empty">
        {t("empty")}
      </p>
    );
  }
  return (
    <ul className="space-y-2 text-sm" data-testid="backup-destinations-readonly">
      {destinations.map((d, i) => (
        <li key={i} className="flex justify-between rounded-md border px-3 py-2">
          <span>
            <span className="font-medium">{d.name}</span>{" "}
            <span className="text-muted-foreground">({t(TYPE_KEYS[d.type])})</span>
          </span>
          <span className="text-muted-foreground">
            {d.enabled ? t("enabledLabel") : t("disabledLabel")}
          </span>
        </li>
      ))}
    </ul>
  );
}
