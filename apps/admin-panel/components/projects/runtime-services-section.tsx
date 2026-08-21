"use client";

// ADR 0129 fase 2: superficie de configuración de los SERVICIOS de respaldo del
// proyecto (MySQL/MariaDB/Postgres/Redis/Beanstalkd o una imagen arbitraria) +
// variables de entorno + una imagen de runtime custom. El worker traduce
// `repository_config.services`/`env` a sidecars endurecidos en un bridge interno
// y deriva la connection-env (DATABASE_URL/REDIS_URL/…) que inyecta en el
// contenedor de stack_exec, los tests de aceptación y el app-preview del review.
// Los servicios del catálogo derivan su connection-string solos; para una imagen
// arbitraria, fija tú la cadena de conexión en las variables de entorno.
//
// i18n (prod-16 `task_prod16_03`): `validate()` NO devuelve texto, devuelve la
// clave del diccionario y sus variables. Devolver texto obligaba a la función
// pura a conocer el idioma, y en la práctica significaba que los siete mensajes
// de validación salían en castellano con el toggle en EN — justo cuando el
// operador está corrigiendo un formulario.

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Boxes, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { apiFetch } from "@/lib/api";
import { useT, type MessageKey, type TranslationVars, type Translator } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

/** Tipos de servicio del catálogo cerrado (ADR 0129 §1) + la escotilla de imagen. */
const CATALOG_TYPES = ["mysql", "mariadb", "postgres", "redis", "beanstalkd"] as const;
const IMAGE_KIND = "__image__";

type ServicesKey = MessageKey<"projectRuntimeServices">;
type ServicesT = Translator<"projectRuntimeServices">;

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
  const t = useT("projectRuntimeServices");
  const errorText = useErrorText();
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
      setErrorMsg(errorText(e));
    },
  });

  return (
    <Card data-testid="runtime-services-section">
      <CardHeader className="flex flex-row items-center gap-2">
        <Boxes className="text-muted-foreground h-5 w-5" />
        <CardTitle>{t("title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <p className="text-muted-foreground text-sm">{t("description")}</p>

        {/* --- Servicios --- */}
        <div className="space-y-3">
          <Label>{t("servicesLabel")}</Label>
          {services.length === 0 ? (
            <p className="text-muted-foreground text-xs">{t("servicesEmpty")}</p>
          ) : null}
          <div className="space-y-2">
            {services.map((svc, i) => (
              <ServiceRowEditor
                key={i}
                row={svc}
                t={t}
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
            {t("addService")}
          </Button>
        </div>

        {/* --- Variables de entorno --- */}
        <div className="space-y-3">
          <Label>{t("envLabel")}</Label>
          <p className="text-muted-foreground text-xs">{t("envHint")}</p>
          <div className="space-y-2">
            {env.map((row, i) => (
              <div
                key={i}
                className="grid grid-cols-[1fr_1fr_auto] gap-2"
                data-testid={`env-row-${i}`}
              >
                <Input
                  aria-label={t("envKeyLabel")}
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
                  aria-label={t("envValueLabel")}
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
                  aria-label={t("removeEnv")}
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
            {t("addEnv")}
          </Button>
        </div>

        {/* --- Imagen de runtime custom --- */}
        <div className="space-y-1.5">
          <Label htmlFor="runtime-image">{t("runtimeImageLabel")}</Label>
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
            {t("runtimeImageHintBefore")}
            <code>FROM agentic-platform/agent-runtime-php-phpunit:v1</code>
            {t("runtimeImageHintAfter")}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => save.mutate()}
            disabled={save.isPending || !validation.ok}
            data-testid="runtime-services-save"
          >
            {t("save")}
          </Button>
          {!validation.ok ? (
            <p className="text-destructive text-xs" data-testid="runtime-services-validation">
              {t(validation.key, validation.vars)}
            </p>
          ) : null}
          {saved ? <p className="text-success text-xs">{t("saved")}</p> : null}
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
  t,
  onChange,
  onRemove,
  index,
}: {
  row: ServiceRow;
  t: ServicesT;
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
        aria-label={t("serviceTypeLabel")}
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
        {/* Los tipos del catálogo son los identificadores que viajan al backend. */}
        {CATALOG_TYPES.map((type) => (
          <option key={type} value={type}>
            {type}
          </option>
        ))}
        <option value={IMAGE_KIND}>{t("serviceImageOption")}</option>
      </Select>

      {row.kind === "catalog" ? (
        <Input
          aria-label={t("serviceVersionLabel")}
          placeholder={t("serviceVersionPlaceholder")}
          value={row.version}
          data-testid={`service-version-${index}`}
          onChange={(e) => onChange({ ...row, version: e.target.value })}
        />
      ) : (
        <Input
          aria-label={t("serviceImageLabel")}
          placeholder="rabbitmq:3-management"
          value={row.image}
          data-testid={`service-image-${index}`}
          onChange={(e) => onChange({ ...row, image: e.target.value })}
        />
      )}

      <Input
        aria-label={t("serviceAliasLabel")}
        placeholder={t("serviceAliasPlaceholder")}
        value={row.alias}
        data-testid={`service-alias-${index}`}
        onChange={(e) => onChange({ ...row, alias: e.target.value })}
      />

      <Button
        variant="ghost"
        size="sm"
        aria-label={t("removeService")}
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

/** El problema, como CLAVE del diccionario + sus variables. Ver la cabecera. */
type Validation = { ok: true } | { ok: false; key: ServicesKey; vars?: TranslationVars };

function validate(services: ServiceRow[], env: EnvRow[], runtimeImage: string): Validation {
  const aliases = new Set<string>();
  for (const s of services) {
    const alias = s.alias.trim();
    if (alias && !ALIAS_RE.test(alias)) {
      return { ok: false, key: "invalidAlias", vars: { alias } };
    }
    if (alias) {
      if (aliases.has(alias)) return { ok: false, key: "duplicateAlias", vars: { alias } };
      aliases.add(alias);
    }
    if (s.kind === "image") {
      if (!s.image.trim()) return { ok: false, key: "imageNeedsTag" };
      if (!IMAGE_RE.test(s.image.trim())) {
        return { ok: false, key: "invalidImage", vars: { image: s.image.trim() } };
      }
      if (!alias) return { ok: false, key: "imageNeedsAlias" };
    }
  }
  if (services.length > 8) return { ok: false, key: "tooManyServices" };
  for (const { key } of env) {
    const k = key.trim();
    if (k && !ENV_KEY_RE.test(k)) {
      return { ok: false, key: "invalidEnvKey", vars: { key: k } };
    }
  }
  const img = runtimeImage.trim();
  if (img && !IMAGE_RE.test(img)) {
    return { ok: false, key: "invalidRuntimeImage", vars: { image: img } };
  }
  return { ok: true };
}
