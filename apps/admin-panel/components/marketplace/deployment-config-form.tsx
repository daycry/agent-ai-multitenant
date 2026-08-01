"use client";

/**
 * El formulario guiado del despliegue (ADR 0142, `task_mkt2_06`).
 *
 * Es LA pieza que reutilizan las tres puertas —ficha de la instalación, paso
 * «Capacidades» del wizard y pestañas del proyecto—, y por eso vive en
 * `components/` y no dentro de ninguna de ellas: tres copias del mismo
 * formulario es exactamente el modo en que las dos superficies de UI empiezan a
 * divergir, que es lo que el ADR 0142 existe para impedir.
 *
 * Generaliza lo que hasta `task_mkt2_13` sólo sabía hacer la pantalla de
 * Playwright (`marketplace/listings/[id]/playwright-config`, ya BORRADA):
 * aquella pintaba a mano los seis campos de su `config_schema` y los pedía al
 * instalar; ésta los deriva del esquema y los pide al desplegar, así que
 * cualquier listing que declare uno tiene formulario sin escribir una línea.
 *
 * **Controlado del todo**: no guarda estado. El borrador (`values` + `roles`) lo
 * lleva el padre como dato plano, porque el wizard maneja N capacidades a la vez
 * y un hook por capacidad sería llamar hooks en un bucle.
 *
 * El submit tampoco está aquí: cada puerta submitea a su manera (una por
 * proyecto en la ficha, todas de golpe al crear en el wizard). Lo que sí es
 * contrato común es que el submit se bloquea mientras `draftErrors` no esté
 * vacío, y los errores se ven — nunca un botón muerto sin explicación.
 */

import { Fragment } from "react";

import { AGENT_ROLES, ROLE_LABEL } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useT } from "@/lib/i18n";

import {
  VAULT_POINTER_PREFIX,
  draftErrors,
  schemaFields,
  type CapabilityShape,
  type ConfigError,
  type ConfigSchemaField,
  type DeploymentDraft,
} from "./deployment-types";

/** Código de error → clave del diccionario. Un `Record` para que TS exija los diez. */
const ERROR_KEY = {
  required: "errRequired",
  type: "errType",
  enum: "errEnum",
  itemEnum: "errItemEnum",
  minItems: "errMinItems",
  min: "errMin",
  max: "errMax",
  secretNotVaultPointer: "errSecretNotVaultPointer",
  secretPointerEmpty: "errSecretPointerEmpty",
  unknown: "errUnknown",
} as const satisfies Record<ConfigError["code"], string>;

export interface DeploymentConfigFormProps {
  /** Prefijo de los `data-testid`, para que dos formularios convivan en la misma página. */
  idPrefix: string;
  capability: CapabilityShape;
  draft: DeploymentDraft;
  onChange: (draft: DeploymentDraft) => void;
  /** Deshabilita todo mientras el POST está en vuelo. */
  disabled?: boolean;
}

export function DeploymentConfigForm({
  idPrefix,
  capability,
  draft,
  onChange,
  disabled = false,
}: DeploymentConfigFormProps) {
  const t = useT("marketplaceDeploy");
  const fields = schemaFields(capability.config_schema);
  const errors = draftErrors(capability, draft);

  function setValue(name: string, value: unknown) {
    onChange({ ...draft, values: { ...draft.values, [name]: value } });
  }

  function toggleRole(role: (typeof AGENT_ROLES)[number]) {
    const has = draft.roles.includes(role);
    const next = has ? draft.roles.filter((r) => r !== role) : [...draft.roles, role];
    // Orden canónico siempre: dos despliegues del mismo listing mandan la misma
    // lista y el diff de una auditoría no es ruido de ordenación.
    onChange({ ...draft, roles: AGENT_ROLES.filter((r) => next.includes(r)) });
  }

  return (
    <div className="space-y-4" data-testid={`${idPrefix}-form`}>
      {/* ---------------------------- config ---------------------------- */}
      {fields.length === 0 ? (
        <p className="text-muted-foreground text-xs" data-testid={`${idPrefix}-no-config`}>
          {t("noConfigNeeded")}
        </p>
      ) : (
        <section className="space-y-3" data-testid={`${idPrefix}-config`}>
          <div>
            <h4 className="text-sm font-semibold">{t("configTitle")}</h4>
            <p className="text-muted-foreground text-xs">{t("configHelp")}</p>
          </div>
          {fields.map(({ name, spec }) => (
            <Fragment key={name}>
              <FieldRow
                idPrefix={idPrefix}
                name={name}
                spec={spec}
                value={draft.values[name]}
                disabled={disabled}
                onChange={(next) => setValue(name, next)}
                secretHelp={t("secretHelp", { prefix: VAULT_POINTER_PREFIX })}
              />
            </Fragment>
          ))}
        </section>
      )}

      {/* ----------------------------- roles ---------------------------- */}
      <section className="space-y-2" data-testid={`${idPrefix}-roles`}>
        <div>
          <h4 className="text-sm font-semibold">{t("rolesTitle")}</h4>
          <p className="text-muted-foreground text-xs">{t("rolesHelp")}</p>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {AGENT_ROLES.map((role) => (
            <label key={role} className="flex cursor-pointer items-center gap-1.5 text-sm">
              <Checkbox
                checked={draft.roles.includes(role)}
                disabled={disabled}
                onChange={() => toggleRole(role)}
                data-testid={`${idPrefix}-role-${role}`}
              />
              {ROLE_LABEL[role]}
            </label>
          ))}
        </div>
        {draft.roles.length === 0 ? (
          <p
            className="text-warning-soft-foreground text-xs"
            data-testid={`${idPrefix}-roles-empty-warning`}
          >
            {t("rolesEmptyWarning")}
          </p>
        ) : null}
      </section>

      {/* ---------------------------- errores --------------------------- */}
      {errors.length > 0 ? (
        <ul className="text-destructive space-y-0.5 text-xs" data-testid={`${idPrefix}-errors`}>
          {errors.map((error) => (
            <li key={`${error.field}-${error.code}`}>
              {t(ERROR_KEY[error.code], { field: error.field, detail: error.detail ?? "" })}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Una fila del formulario, elegida por el dialecto del `config_schema`
// ---------------------------------------------------------------------------
function FieldRow({
  idPrefix,
  name,
  spec,
  value,
  disabled,
  onChange,
  secretHelp,
}: {
  idPrefix: string;
  name: string;
  spec: ConfigSchemaField;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
  secretHelp: string;
}) {
  const testId = `${idPrefix}-field-${name}`;
  const label = spec.title ?? name;
  const hint = spec.description;

  // --- booleano -----------------------------------------------------------
  if (spec.type === "boolean" && !spec.secret) {
    return (
      <label className="flex cursor-pointer items-start gap-2 text-sm">
        <Checkbox
          checked={value === true}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          data-testid={testId}
        />
        <span>
          {label}
          {hint ? <span className="text-muted-foreground block text-xs">{hint}</span> : null}
        </span>
      </label>
    );
  }

  // --- array con `items.enum`: chips multi-selección -----------------------
  if (spec.type === "array" && Array.isArray(spec.items?.enum)) {
    const selected = Array.isArray(value) ? value : [];
    return (
      <div className="space-y-1">
        <Label>{label}</Label>
        <div className="flex flex-wrap gap-2" data-testid={testId}>
          {spec.items.enum.map((option) => {
            const key = String(option);
            const on = selected.includes(option);
            return (
              <Button
                key={key}
                type="button"
                size="sm"
                variant={on ? "default" : "outline"}
                disabled={disabled}
                aria-pressed={on}
                data-testid={`${idPrefix}-item-${name}-${key}`}
                onClick={() =>
                  onChange(
                    on
                      ? selected.filter((entry) => entry !== option)
                      : // Se conserva el orden del enum del manifest, no el de clic.
                        (spec.items?.enum ?? []).filter(
                          (entry) => entry === option || selected.includes(entry),
                        ),
                  )
                }
              >
                {key}
              </Button>
            );
          })}
        </div>
        {hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
      </div>
    );
  }

  // --- enum: desplegable ---------------------------------------------------
  if (Array.isArray(spec.enum) && spec.enum.length > 0) {
    return (
      <div className="space-y-1">
        <Label htmlFor={testId}>{label}</Label>
        <Select
          id={testId}
          value={value === null || value === undefined ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          data-testid={testId}
        >
          {spec.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </Select>
        {hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
      </div>
    );
  }

  // --- número --------------------------------------------------------------
  if ((spec.type === "integer" || spec.type === "number") && !spec.secret) {
    return (
      <div className="space-y-1">
        <Label htmlFor={testId}>{label}</Label>
        <Input
          id={testId}
          type="number"
          min={spec.minimum}
          max={spec.maximum}
          value={value === null || value === undefined ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(null);
              return;
            }
            const parsed = spec.type === "integer" ? Number.parseInt(raw, 10) : Number(raw);
            // Un NaN no se manda: se deja el campo vacío y el validador lo canta
            // como requerido, en vez de enviar `NaN` al backend.
            onChange(Number.isNaN(parsed) ? null : parsed);
          }}
          data-testid={testId}
        />
        {hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
      </div>
    );
  }

  // --- texto (y el caso `secret`) -----------------------------------------
  return (
    <div className="space-y-1">
      <Label htmlFor={testId}>
        {label}
        {spec.secret ? (
          <Badge variant="warning" className="ml-2">
            {VAULT_POINTER_PREFIX}
          </Badge>
        ) : null}
      </Label>
      <Input
        id={testId}
        value={value === null || value === undefined ? "" : String(value)}
        disabled={disabled}
        placeholder={spec.secret ? `${VAULT_POINTER_PREFIX}kv/data/…` : undefined}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        data-testid={testId}
      />
      {spec.secret ? (
        <p
          className="text-muted-foreground text-xs"
          data-testid={`${idPrefix}-secret-help-${name}`}
        >
          {secretHelp}
        </p>
      ) : hint ? (
        <p className="text-muted-foreground text-xs">{hint}</p>
      ) : null}
    </div>
  );
}
