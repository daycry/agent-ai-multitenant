"use client";

// ADR 0129 fase 2: superficie de configuración de los SERVICIOS de respaldo del
// proyecto (MySQL/MariaDB/Postgres/Redis/Beanstalkd o una imagen arbitraria) +
// variables de entorno + una imagen de runtime custom. El worker traduce
// `repository_config.services`/`env` a sidecars endurecidos en un bridge interno
// y deriva la connection-env (DATABASE_URL/REDIS_URL/…) que inyecta en el
// contenedor de stack_exec, los tests de aceptación y el app-preview del review.
// Los servicios del catálogo derivan su connection-string solos; para una imagen
// arbitraria, fija tú la cadena de conexión en las variables de entorno.

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Boxes, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";

/** Tipos de servicio del catálogo cerrado (ADR 0129 §1) + la escotilla de imagen. */
const CATALOG_TYPES = ["mysql", "mariadb", "postgres", "redis", "beanstalkd"] as const;
const IMAGE_KIND = "__image__";

interface RuntimeServicesSectionProps {
  projectId: string;
  /** repository_config actual del proyecto (null = nunca configurado). */
  value: Record<string, unknown> | null;
}

type ServiceRow =
  | { kind: "catalog"; type: string; version: string; alias: string }
  | { kind: "image"; image: string; alias: string };

interface EnvRow {
  key: string;
  value: string;
}

const ALIAS_RE = /^[a-z][a-z0-9-]{0,30}$/;
const ENV_KEY_RE = /^[A-Z][A-Z0-9_]*$/;
const IMAGE_RE = /^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$/;

function parseServices(raw: unknown): ServiceRow[] {
  if (!Array.isArray(raw)) return [];
  const rows: ServiceRow[] = [];
  for (const entry of raw) {
    if (entry == null || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    if (typeof e["type"] === "string") {
      rows.push({
        kind: "catalog",
        type: e["type"],
        version: typeof e["version"] === "string" ? e["version"] : "",
        alias: typeof e["alias"] === "string" ? e["alias"] : "",
      });
    } else if (typeof e["image"] === "string") {
      rows.push({
        kind: "image",
        image: e["image"],
        alias: typeof e["alias"] === "string" ? e["alias"] : "",
      });
    }
  }
  return rows;
}

function parseEnv(raw: unknown): EnvRow[] {
  if (raw == null || typeof raw !== "object") return [];
  return Object.entries(raw as Record<string, unknown>).map(([key, v]) => ({
    key,
    value: String(v),
  }));
}

export function RuntimeServicesSection({ projectId, value }: RuntimeServicesSectionProps) {
  const queryClient = useQueryClient();
  const [services, setServices] = useState<ServiceRow[]>(() => parseServices(value?.["services"]));
  const [env, setEnv] = useState<EnvRow[]>(() => parseEnv(value?.["env"]));
  const [runtimeImage, setRuntimeImage] = useState(
    typeof value?.["runtime_image"] === "string" ? (value["runtime_image"] as string) : "",
  );
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const touched = () => {
    setSaved(false);
    setErrorMsg(null);
  };

  const validation = useMemo(
    () => validate(services, env, runtimeImage),
    [services, env, runtimeImage],
  );

  const save = useMutation({
    mutationFn: async () => {
      // Merge sobre el blob existente: esta sección solo posee services/env/runtime_image.
      const next: Record<string, unknown> = { ...(value ?? {}) };
      // No poseemos las claves de plataforma (git-sync / app-preview): quítalas del
      // payload para que el servidor las conserve frescas de la BD y no pisemos un
      // sync concurrente ni la imagen del review (ADR 0129 respeta review-preview).
      delete next["last_git_sync"];
      delete next["review_image"];
      const serialized = services
        .map((s) => serializeService(s))
        .filter((s): s is Record<string, unknown> => s !== null);
      if (serialized.length) next["services"] = serialized;
      else delete next["services"];

      const envObj: Record<string, string> = {};
      for (const { key, value: v } of env) {
        if (key.trim()) envObj[key.trim()] = v;
      }
      if (Object.keys(envObj).length) next["env"] = envObj;
      else delete next["env"];

      const img = runtimeImage.trim();
      if (img) next["runtime_image"] = img;
      else delete next["runtime_image"];

      return apiFetch(`/projects/${projectId}`, {
        method: "PUT",
        body: { repository_config: next },
      });
    },
    onSuccess: () => {
      setErrorMsg(null);
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => {
      setSaved(false);
      setErrorMsg(e instanceof ApiError ? e.body : String(e));
    },
  });

  return (
    <Card data-testid="runtime-services-section">
      <CardHeader className="flex flex-row items-center gap-2">
        <Boxes className="text-muted-foreground h-5 w-5" />
        <CardTitle>Servicios e imagen de runtime</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <p className="text-muted-foreground text-sm">
          Servicios de respaldo (base de datos, caché, colas) que la plataforma levanta como
          sidecars endurecidos junto al runtime del proyecto, para que sus tests y el app-preview
          arranquen. Los servicios del catálogo derivan su cadena de conexión automáticamente
          (`DATABASE_URL`, `REDIS_URL`, …); para una imagen arbitraria, fija tú la conexión en las
          variables de entorno. Aíslados en una red interna por tarea/sesión (ADR 0129).
        </p>

        {/* --- Servicios --- */}
        <div className="space-y-3">
          <Label>Servicios</Label>
          {services.length === 0 ? (
            <p className="text-muted-foreground text-xs">Sin servicios declarados.</p>
          ) : null}
          <div className="space-y-2">
            {services.map((svc, i) => (
              <ServiceRowEditor
                key={i}
                row={svc}
                onChange={(next) => {
                  touched();
                  setServices((prev) => prev.map((s, j) => (j === i ? next : s)));
                }}
                onRemove={() => {
                  touched();
                  setServices((prev) => prev.filter((_, j) => j !== i));
                }}
                index={i}
              />
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            data-testid="add-service"
            onClick={() => {
              touched();
              setServices((prev) => [
                ...prev,
                { kind: "catalog", type: "mysql", version: "", alias: "" },
              ]);
            }}
          >
            <Plus className="mr-1 h-4 w-4" />
            Añadir servicio
          </Button>
        </div>

        {/* --- Variables de entorno --- */}
        <div className="space-y-3">
          <Label>Variables de entorno</Label>
          <p className="text-muted-foreground text-xs">
            Inyectadas en el contenedor principal (tests / app-preview). Sobrescriben la
            connection-env derivada si repites la clave. No es Vault: no pongas secretos de
            producción aquí.
          </p>
          <div className="space-y-2">
            {env.map((row, i) => (
              <div
                key={i}
                className="grid grid-cols-[1fr_1fr_auto] gap-2"
                data-testid={`env-row-${i}`}
              >
                <Input
                  aria-label="Clave"
                  placeholder="APP_ENV"
                  value={row.key}
                  data-testid={`env-key-${i}`}
                  onChange={(e) => {
                    touched();
                    setEnv((prev) =>
                      prev.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)),
                    );
                  }}
                />
                <Input
                  aria-label="Valor"
                  placeholder="testing"
                  value={row.value}
                  data-testid={`env-value-${i}`}
                  onChange={(e) => {
                    touched();
                    setEnv((prev) =>
                      prev.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)),
                    );
                  }}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label="Quitar variable"
                  data-testid={`env-remove-${i}`}
                  onClick={() => {
                    touched();
                    setEnv((prev) => prev.filter((_, j) => j !== i));
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
          <Button
            variant="outline"
            size="sm"
            data-testid="add-env"
            onClick={() => {
              touched();
              setEnv((prev) => [...prev, { key: "", value: "" }]);
            }}
          >
            <Plus className="mr-1 h-4 w-4" />
            Añadir variable
          </Button>
        </div>

        {/* --- Imagen de runtime custom --- */}
        <div className="space-y-1.5">
          <Label htmlFor="runtime-image">Imagen de runtime custom (opcional)</Label>
          <Input
            id="runtime-image"
            data-testid="runtime-image-input"
            placeholder="agentic-platform/agent-runtime-php-phpunit:v1"
            value={runtimeImage}
            onChange={(e) => {
              touched();
              setRuntimeImage(e.target.value);
            }}
          />
          <p className="text-muted-foreground text-xs">
            Solo si necesitas paquetes/extensiones de sistema no cubiertos por los comandos del
            proyecto. Básala en un runtime-template de la plataforma (p.ej.{" "}
            <code>FROM agentic-platform/agent-runtime-php-phpunit:v1</code>) e instala lo que falte;
            la publica tu CI (la plataforma no la construye, ADR 0129). Vacío = usa el runtime por
            defecto del proyecto.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => save.mutate()}
            disabled={save.isPending || !validation.ok}
            data-testid="runtime-services-save"
          >
            Guardar servicios
          </Button>
          {!validation.ok ? (
            <p className="text-destructive text-xs" data-testid="runtime-services-validation">
              {validation.message}
            </p>
          ) : null}
          {saved ? <p className="text-success text-xs">Guardado.</p> : null}
          {errorMsg ? (
            <p className="text-destructive text-xs" data-testid="runtime-services-error">
              {errorMsg}
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function ServiceRowEditor({
  row,
  onChange,
  onRemove,
  index,
}: {
  row: ServiceRow;
  onChange: (next: ServiceRow) => void;
  onRemove: () => void;
  index: number;
}) {
  const kindValue = row.kind === "image" ? IMAGE_KIND : row.type;
  return (
    <div
      className="grid grid-cols-1 gap-2 rounded border p-2 sm:grid-cols-[10rem_1fr_1fr_auto]"
      data-testid={`service-row-${index}`}
    >
      <Select
        aria-label="Tipo de servicio"
        value={kindValue}
        data-testid={`service-type-${index}`}
        onChange={(e) => {
          const v = e.target.value;
          if (v === IMAGE_KIND) {
            onChange({ kind: "image", image: "", alias: "" });
          } else {
            onChange({ kind: "catalog", type: v, version: "", alias: "" });
          }
        }}
      >
        {CATALOG_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
        <option value={IMAGE_KIND}>imagen…</option>
      </Select>

      {row.kind === "catalog" ? (
        <Input
          aria-label="Versión"
          placeholder="versión (ej. 8.4) — vacío = por defecto"
          value={row.version}
          data-testid={`service-version-${index}`}
          onChange={(e) => onChange({ ...row, version: e.target.value })}
        />
      ) : (
        <Input
          aria-label="Imagen"
          placeholder="rabbitmq:3-management"
          value={row.image}
          data-testid={`service-image-${index}`}
          onChange={(e) => onChange({ ...row, image: e.target.value })}
        />
      )}

      <Input
        aria-label="Alias (hostname)"
        placeholder="alias/hostname (vacío = tipo)"
        value={row.alias}
        data-testid={`service-alias-${index}`}
        onChange={(e) => onChange({ ...row, alias: e.target.value })}
      />

      <Button
        variant="ghost"
        size="sm"
        aria-label="Quitar servicio"
        data-testid={`service-remove-${index}`}
        onClick={onRemove}
      >
        <Trash2 className="h-4 w-4" />
      </Button>
    </div>
  );
}

function serializeService(s: ServiceRow): Record<string, unknown> | null {
  if (s.kind === "catalog") {
    const out: Record<string, unknown> = { type: s.type };
    if (s.version.trim()) out["version"] = s.version.trim();
    if (s.alias.trim()) out["alias"] = s.alias.trim();
    return out;
  }
  if (!s.image.trim()) return null;
  const out: Record<string, unknown> = { image: s.image.trim() };
  if (s.alias.trim()) out["alias"] = s.alias.trim();
  return out;
}

function validate(
  services: ServiceRow[],
  env: EnvRow[],
  runtimeImage: string,
): { ok: true } | { ok: false; message: string } {
  const aliases = new Set<string>();
  for (const s of services) {
    const alias = s.alias.trim();
    if (alias && !ALIAS_RE.test(alias)) {
      return { ok: false, message: `Alias inválido: ${alias} (usa [a-z][a-z0-9-]*).` };
    }
    if (alias) {
      if (aliases.has(alias)) return { ok: false, message: `Alias duplicado: ${alias}.` };
      aliases.add(alias);
    }
    if (s.kind === "image") {
      if (!s.image.trim()) return { ok: false, message: "Una imagen de servicio requiere un tag." };
      if (!IMAGE_RE.test(s.image.trim())) {
        return { ok: false, message: `Imagen inválida: ${s.image.trim()}.` };
      }
      if (!alias) return { ok: false, message: "Una imagen de servicio requiere un alias." };
    }
  }
  if (services.length > 8) return { ok: false, message: "Máximo 8 servicios." };
  for (const { key } of env) {
    const k = key.trim();
    if (k && !ENV_KEY_RE.test(k)) {
      return { ok: false, message: `Variable inválida: ${k} (usa [A-Z][A-Z0-9_]*).` };
    }
  }
  const img = runtimeImage.trim();
  if (img && !IMAGE_RE.test(img)) {
    return { ok: false, message: `Imagen de runtime inválida: ${img}.` };
  }
  return { ok: true };
}
