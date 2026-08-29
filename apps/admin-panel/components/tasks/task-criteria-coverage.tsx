"use client";

/**
 * `CriteriaCoverage` — cuánto de una tarea se comprueba a máquina, cuánto lo
 * mira una persona y cuánto no lo ha declarado nadie (ADR 0162).
 *
 * Antes de esto la ficha resumía la cobertura en una frase: «1 de 3 criterios se
 * comprueban solos». Esa frase tiene dos defectos, y son los dos que este
 * componente existe para corregir.
 *
 * **El primero: aplasta tres estados en dos.** «Declarado manual, y este es el
 * motivo» y «nadie ha dicho nada» caían igual en el lado del «no», y son cosas
 * distintas: la primera es una decisión escrita y auditable, la segunda es el
 * silencio que el ADR denuncia («un valor ausente no puede significar nada más
 * fuerte que *desconocido*»). Quien mira la ficha necesita poder distinguirlas
 * sin abrir el editor criterio a criterio.
 *
 * **El segundo: convierte en carencia lo que no lo es.** Una tarea de análisis,
 * un ADR o una tarea de documentación tienen legítimamente CERO comprobaciones
 * automáticas, y «0 de 3 se comprueban solos» las acusa de un defecto que no
 * tienen. Ese es el falso fallo del encargo, en versión visual. Por eso las
 * categorías vacías **no se pintan**: el resumen enumera lo que hay, no lo que
 * falta, y sólo aparece un aviso cuando hay criterios sin declarar — que es el
 * único caso en el que de verdad falta información.
 *
 * Esto **informa y no decide**: no deshabilita nada, no degrada ningún
 * veredicto y no impide guardar. El gate es la opción C del ADR 0162, sigue sin
 * firmar, y es justo donde viven los falsos fallos.
 */

import { Fragment } from "react";

import { criteriaCheckSummary, type CriterionCheckState } from "@/lib/acceptance-criteria";
import { useT } from "@/lib/i18n";

type CoverageKey = "coverageAutomated" | "coverageManual" | "coverageUndeclared";

/**
 * Orden de lectura: primero lo que la máquina comprueba, luego lo que comprueba
 * una persona y al final lo que no comprueba nadie. Es el orden de menor a mayor
 * incertidumbre, no un ranking de calidad.
 */
const CHIPS: { state: CriterionCheckState; key: CoverageKey }[] = [
  { state: "automated", key: "coverageAutomated" },
  { state: "manual", key: "coverageManual" },
  { state: "undeclared", key: "coverageUndeclared" },
];

export function CriteriaCoverage({ criteria }: { criteria: readonly unknown[] }) {
  const t = useT("taskDetail");
  const summary = criteriaCheckSummary(criteria);
  if (summary.total === 0) return null;

  const shown = CHIPS.filter((chip) => summary[chip.state] > 0);

  return (
    <div className="mb-1" data-testid="task-criteria-coverage">
      <p className="text-muted-foreground text-xs">
        {t("coverageLabel")}{" "}
        {shown.map((chip, i) => (
          <Fragment key={chip.state}>
            {i > 0 ? " · " : null}
            <span data-testid={`task-criteria-coverage-${chip.state}`}>
              {t(chip.key, { count: summary[chip.state] })}
            </span>
          </Fragment>
        ))}
      </p>
      {/* El aviso sale SÓLO con criterios sin declarar, porque es el único caso
          en el que hay algo que hacer. Enseñarlo siempre lo volvería ruido de
          fondo y acabaría leyéndose como un reproche a las tareas que están
          bien. */}
      {summary.undeclared > 0 ? (
        <p className="text-muted-foreground text-xs" data-testid="task-criteria-coverage-note">
          {t("checkUndeclaredHint")}
        </p>
      ) : null}
    </div>
  );
}
