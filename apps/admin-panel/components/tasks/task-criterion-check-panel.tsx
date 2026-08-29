"use client";

/**
 * `CriterionCheckPanel` — los campos con los que un humano declara **cómo** se
 * comprueba un criterio de aceptación (ADR 0162, opción A por la vía del
 * operador).
 *
 * Antes de esto la fila de un criterio era un `<input>` suelto que emitía
 * siempre una cadena, y el worker sólo ejecuta los criterios que son un dict con
 * `runtime` y `command`: **no había ningún camino humano** para decir que una
 * tarea se verifica ejecutando algo. Este panel es ese camino.
 *
 * Va aparte del `-section` por tamaño y por foco: la sección gobierna la lista
 * (añadir, quitar, guardar, comparar con la propuesta de la IA) y este panel
 * gobierna UNA declaración. Son dos cosas que cambian por motivos distintos.
 *
 * Los `runtime` NO se escriben a mano: salen de `GET /runtime-templates`, que ya
 * sirve el catálogo de 14 plantillas con su rótulo ES/EN. Un id inventado aquí
 * haría reventar al worker con un `KeyError` que el operador leería como «mi
 * repositorio está roto».
 */

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { CHECK_TYPES, type CheckType, type CriterionCheck } from "@/lib/acceptance-criteria";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { runtimeLabel, useRuntimeTemplates } from "@/lib/runtime-templates";

/** Rótulo de cada `check_type` en el desplegable. */
const CHECK_TYPE_LABEL: Record<CheckType, "checkTypeManual" | "checkTypeAutomated"> = {
  manual: "checkTypeManual",
  automated: "checkTypeAutomated",
};

export function CriterionCheckPanel({
  check,
  index,
  idPrefix,
  onChange,
}: {
  check: CriterionCheck;
  /** Posición de la fila: sólo para los `data-testid` y los `id` de las labels. */
  index: number;
  idPrefix: string;
  onChange: (next: CriterionCheck) => void;
}) {
  const t = useT("taskDetail");
  const lang = useLangOptional();
  const runtimes = useRuntimeTemplates();

  const typeId = `${idPrefix}-type-${index}`;
  const runtimeId = `${idPrefix}-runtime-${index}`;
  const commandId = `${idPrefix}-command-${index}`;
  const signalId = `${idPrefix}-signal-${index}`;
  const reasonId = `${idPrefix}-reason-${index}`;

  // Un `<select>` cuyo valor todavía no tiene `<option>` pinta la PRIMERA como
  // elegida: mientras el catálogo carga, el desplegable diría «elige un
  // runtime…» sobre un criterio que ya tiene uno. La opción-marcador conserva el
  // valor y explica por qué no se ve su nombre.
  const templates = runtimes.data ?? [];
  const known = templates.some((tpl) => tpl.id === check.runtime);
  const missingRuntimeLabel = runtimes.isLoading
    ? t("checkRuntimeLoading")
    : t("checkRuntimeUnknown", { id: check.runtime });

  return (
    <fieldset
      className="border-border mt-2 space-y-2 rounded-md border p-3"
      data-testid={`task-criterion-check-${index}`}
    >
      <legend className="text-muted-foreground px-1 text-xs font-semibold uppercase tracking-wide">
        {t("checkHeading")}
      </legend>

      <div className="space-y-1">
        <Label htmlFor={typeId}>{t("checkTypeLabel")}</Label>
        <Select
          id={typeId}
          value={check.checkType}
          onChange={(e) => onChange({ ...check, checkType: e.target.value as CheckType })}
          data-testid={`task-criterion-check-type-${index}`}
        >
          {CHECK_TYPES.map((value) => (
            <option key={value} value={value}>
              {t(CHECK_TYPE_LABEL[value])}
            </option>
          ))}
        </Select>
      </div>

      {check.checkType === "automated" ? (
        <>
          <div className="space-y-1">
            <Label htmlFor={runtimeId}>{t("checkRuntimeLabel")}</Label>
            <Select
              id={runtimeId}
              value={check.runtime}
              onChange={(e) => onChange({ ...check, runtime: e.target.value })}
              data-testid={`task-criterion-runtime-${index}`}
            >
              <option value="">{t("checkRuntimeNone")}</option>
              {check.runtime && !known ? (
                <option value={check.runtime}>{missingRuntimeLabel}</option>
              ) : null}
              {templates.map((tpl) => (
                <option key={tpl.id} value={tpl.id}>
                  {runtimeLabel(tpl, lang)}
                </option>
              ))}
            </Select>
            {runtimes.isError ? (
              <p
                className="text-destructive text-xs"
                data-testid={`task-criterion-runtime-error-${index}`}
              >
                {t("checkRuntimeError")}
              </p>
            ) : null}
          </div>

          <div className="space-y-1">
            <Label htmlFor={commandId}>{t("checkCommandLabel")}</Label>
            <Input
              id={commandId}
              value={check.command}
              onChange={(e) => onChange({ ...check, command: e.target.value })}
              placeholder={t("checkCommandPlaceholder")}
              className="font-mono text-xs"
              data-testid={`task-criterion-command-${index}`}
            />
            {/* El aviso no es decorativo: en la instalación viva hay dos
                ejecuciones de phpunit con código 0 y «No tests executed!», y la
                plataforma las registró como correctas. */}
            <p className="text-muted-foreground text-xs">{t("checkCommandHint")}</p>
          </div>

          <div className="space-y-1">
            <Label htmlFor={signalId}>{t("checkSignalLabel")}</Label>
            <Input
              id={signalId}
              value={check.expectedSignal}
              onChange={(e) => onChange({ ...check, expectedSignal: e.target.value })}
              className="font-mono text-xs"
              data-testid={`task-criterion-signal-${index}`}
            />
            <p className="text-muted-foreground text-xs">{t("checkSignalHint")}</p>
          </div>
        </>
      ) : (
        <div className="space-y-1">
          <Label htmlFor={reasonId}>{t("checkReasonLabel")}</Label>
          <Input
            id={reasonId}
            value={check.manualReason}
            onChange={(e) => onChange({ ...check, manualReason: e.target.value })}
            placeholder={t("checkReasonPlaceholder")}
            data-testid={`task-criterion-reason-${index}`}
          />
          {/* El motivo es obligatorio a propósito: el ADR 0162 pide que el
              silencio deje de ser una respuesta válida. Marcar algo como
              no-automatizable sin decir por qué es el mismo hueco, firmado. */}
          <p className="text-muted-foreground text-xs">{t("checkManualHint")}</p>
        </div>
      )}
    </fieldset>
  );
}
