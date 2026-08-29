"use client";

/**
 * `CriteriaSection` — los criterios de aceptación de una tarea: los enseña, los
 * edita y —desde el ADR 0162— deja **declarar cómo se comprueba cada uno**.
 *
 * Estaba dentro de `task-detail-sheet.tsx`. Sale de ahí porque el editor de la
 * declaración lo habría dejado en ~850 líneas, y la guarda de tamaño del panel
 * (`check-component-size.mjs`) existe precisamente porque estos ficheros crecen
 * solos: cada feature añade su bloque donde ya está el estado.
 *
 * Dos cosas que esta sección hace y conviene no perder de vista:
 *
 * 1. **Enseña lo que va a pasar, no lo que el criterio dice de sí mismo.** El
 *    rótulo de cada criterio sale de `criterionCheckState`, que espeja al worker
 *    (`_coerce_check` + `_run_task_tests`). Un criterio que se declara
 *    `automated` sin comando aparece como «sin comprobación» porque es lo que
 *    ocurre: el worker lo descarta en silencio.
 * 2. **Enseña la declaración, no sólo su rótulo.** Debajo de cada criterio va el
 *    comando que se va a ejecutar o el motivo por el que no se ejecuta ninguno.
 *    Estaban los dos en la base de datos y sólo se veían abriendo el editor, así
 *    que «esto no se automatiza» era una decisión sin constancia visible — que
 *    es justo lo que el ADR 0162 pide que deje de pasar.
 *
 * El resumen de cobertura (cuántos de cada clase) vive en
 * `task-criteria-coverage.tsx`: es lo único que se lee sin tocar nada, y tenía
 * que poder cambiar de redacción sin abrir el editor de criterios.
 */

import { useId, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { CriteriaCoverage } from "@/components/tasks/task-criteria-coverage";
import { CriterionCheckPanel } from "@/components/tasks/task-criterion-check-panel";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  cleanCriteria,
  criteriaErrors,
  criterionCheckState,
  criterionDeclarationDetail,
  criterionErrors,
  criterionText,
  draftFromCriterion,
  newCheck,
  type CriterionCheck,
  type CriterionCheckState,
  type CriterionDraft,
} from "@/lib/acceptance-criteria";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

/** Editable rows carry a stable key so removing a middle row never steals focus
 * from the inputs React would otherwise reuse by index. */
type CriterionRow = CriterionDraft & { key: number };

/**
 * Cómo se pinta cada estado. El automático NO va en verde: «se va a ejecutar»
 * no es «ha pasado», y confundir las dos cosas es el falso verde que este ADR
 * persigue. El que llama la atención es `undeclared`, que es el estado que hoy
 * tienen todos los criterios de la plataforma sin que nadie lo supiera.
 */
// `undeclared` va en NEUTRO, no en ámbar, y la razón importa: hoy lo están TODOS
// los criterios de la plataforma. Pintar el parque entero de aviso no informa de
// nada —si todo alarma, nada alarma— y además acusa a quien no hizo nada mal: el
// sistema nunca le pidió que declarase. Un aviso que señala a todo el mundo es un
// falso fallo en versión visual, y se distingue igual de bien por la etiqueta.
// Ninguno de los tres es un aviso, y `undeclared` menos que ninguno: hoy lo están
// TODOS los criterios de la plataforma. Pintar el parque entero de ámbar no
// informa —si todo alarma, nada alarma— y acusa a quien no hizo nada mal, porque
// el sistema nunca le pidió que declarase nada. Es un falso fallo en versión
// visual.
//
// Se distinguen igual, sin alarmar: `info` para lo que se comprueba a máquina,
// el tinte de marca para lo que alguien decidió marcar manual, y el neutro para
// el hueco. `default` y `muted` comparten clases, así que no sirven para separar
// dos estados: hay que elegir variantes que de verdad se vean distintas.
const STATE_STYLE: Record<CriterionCheckState, { variant: BadgeVariant; key: LabelKey }> = {
  automated: { variant: "info", key: "checkStateAutomated" },
  manual: { variant: "primary", key: "checkStateManual" },
  undeclared: { variant: "muted", key: "checkStateUndeclared" },
};

type LabelKey = "checkStateAutomated" | "checkStateManual" | "checkStateUndeclared";

export function CriteriaSection({
  projectId,
  taskId,
  criteria,
}: {
  projectId: string;
  taskId: string;
  criteria: unknown[];
}) {
  const errorText = useErrorText();
  const t = useT("taskDetail");
  const queryClient = useQueryClient();
  const idPrefix = useId();
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<CriterionRow[]>([]);
  // A pending AI proposal to confirm against the CURRENT criteria before it can
  // replace them (null = no comparison open). Only used when the task already
  // had criteria — an empty task goes straight to the editor.
  const [proposal, setProposal] = useState<string[] | null>(null);
  const keyer = useRef(0);

  function enterEditWith(drafts: CriterionDraft[]) {
    keyer.current = 0;
    setRows(drafts.map((d) => ({ ...d, key: keyer.current++ })));
    setEditing(true);
  }

  function startEdit() {
    // `draftFromCriterion` siembra además la declaración que el criterio ya
    // traiga, para que editar la descripción no la esconda ni la pierda.
    enterEditWith(criteria.map(draftFromCriterion));
  }

  function patchRow(key: number, patch: Partial<CriterionDraft>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  const mutation = useMutation({
    // El `PUT` devuelve la tarea ENTERA, y con ella se repuebla la caché del
    // detalle: por eso no se tipa como `{acceptance_criteria}`, que dejaría la
    // ficha leyendo un objeto recortado.
    mutationFn: (next: unknown[]) =>
      apiFetch<Record<string, unknown>>(`/projects/${projectId}/tasks/${taskId}`, {
        method: "PUT",
        body: { acceptance_criteria: next },
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["task-detail", taskId], updated);
      setEditing(false);
    },
  });

  // AI generation proposes criteria WITHOUT persisting (the endpoint takes the
  // existing ones into account). Empty task → preload the editor to review;
  // otherwise → open the comparison so a regenerate never overwrites silently.
  const generateMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ acceptance_criteria: string[] }>(
        `/projects/${projectId}/tasks/${taskId}/generate-acceptance-criteria`,
        { method: "POST" },
      ),
    onSuccess: ({ acceptance_criteria }) => {
      const proposed = acceptance_criteria ?? [];
      if (criteria.length === 0) {
        enterEditWith(proposed.map((text) => ({ text, original: null })));
      } else {
        setProposal(proposed);
      }
    },
  });

  function acceptProposal() {
    const proposed = proposal ?? [];
    setProposal(null);
    enterEditWith(proposed.map((text) => ({ text, original: null })));
  }

  if (!editing) {
    return (
      <section className="mb-4" data-testid="task-detail-criteria">
        <div className="mb-1 flex items-center justify-between">
          <h4 className="text-muted-foreground text-xs font-semibold uppercase tracking-wide">
            {t("criteriaHeading")}
          </h4>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending}
              data-testid="task-criteria-generate"
            >
              {generateMutation.isPending
                ? t("criteriaGenerating")
                : criteria.length > 0
                  ? t("criteriaRegenerate")
                  : t("criteriaGenerate")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={startEdit}
              data-testid="task-criteria-edit"
            >
              {t("criteriaEdit")}
            </Button>
          </div>
        </div>
        {criteria.length > 0 ? (
          <>
            <CriteriaCoverage criteria={criteria} />
            <ul className="space-y-1 text-sm">
              {criteria.map((c, i) => {
                const state = criterionCheckState(c);
                const style = STATE_STYLE[state];
                // El comando o el motivo declarados, en la propia lista: son la
                // mitad que convierte «esto no se automatiza» en una decisión
                // auditable, y hasta hoy sólo se veían abriendo el editor.
                const detail = criterionDeclarationDetail(c);
                return (
                  <li key={i} className="flex items-start gap-2">
                    <Badge variant={style.variant} data-testid={`task-criterion-state-${i}`}>
                      {t(style.key)}
                    </Badge>
                    <span>
                      {criterionText(c)}
                      {detail ? (
                        <span
                          className="text-muted-foreground block text-xs"
                          data-testid={`task-criterion-detail-${i}`}
                        >
                          {state === "automated" ? (
                            <>
                              {t("detailCommand")} <code className="font-mono">{detail}</code>
                            </>
                          ) : (
                            `${t("detailReason")} ${detail}`
                          )}
                        </span>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : (
          <p className="text-muted-foreground text-xs italic" data-testid="task-criteria-empty">
            {t("criteriaEmpty")}
          </p>
        )}
        {generateMutation.isError ? (
          <p className="text-destructive mt-1 text-sm" data-testid="task-criteria-generate-error">
            {t("criteriaGenerateError")} {errorText(generateMutation.error)}
          </p>
        ) : null}
        <CriteriaCompareDialog
          open={proposal !== null}
          current={criteria}
          proposed={proposal ?? []}
          onAccept={acceptProposal}
          onCancel={() => setProposal(null)}
        />
      </section>
    );
  }

  const blockingErrors = criteriaErrors(rows);

  return (
    <section className="mb-4" data-testid="task-detail-criteria">
      <h4 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
        {t("criteriaHeading")}
      </h4>
      <div className="space-y-3">
        {rows.map((row, i) => {
          const rowErrors = criterionErrors(row);
          return (
            <div
              key={row.key}
              className="border-border rounded-md border p-2"
              data-testid={`task-criterion-row-${i}`}
            >
              <div className="flex items-center gap-2">
                <Input
                  value={row.text}
                  onChange={(e) => patchRow(row.key, { text: e.target.value })}
                  placeholder={t("criterionPlaceholder")}
                  aria-label={t("criterionTextLabel")}
                  data-testid="task-criterion-input"
                />
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    patchRow(row.key, {
                      // Retirar deja `null`, no `undefined`: la diferencia entre
                      // «nunca declaró nada» y «se retiró la declaración» es lo
                      // que permite limpiar el comando que quedaba puesto.
                      check: row.check ? null : (checkSeed(row) ?? newCheck()),
                    })
                  }
                  data-testid={`task-criterion-declare-${i}`}
                >
                  {row.check ? t("checkUndeclare") : t("checkDeclare")}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setRows((prev) => prev.filter((r) => r.key !== row.key))}
                  data-testid={`task-criterion-remove-${i}`}
                  aria-label={t("criterionRemove")}
                >
                  ×
                </Button>
              </div>
              {row.check ? (
                <CriterionCheckPanel
                  check={row.check}
                  index={i}
                  idPrefix={idPrefix}
                  onChange={(next) => patchRow(row.key, { check: next })}
                />
              ) : null}
              {rowErrors.length > 0 ? (
                <ul
                  className="text-destructive mt-1 space-y-0.5 text-xs"
                  data-testid={`task-criterion-errors-${i}`}
                >
                  {rowErrors.map((key) => (
                    <li key={key}>{t(key)}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            setRows((prev) => [...prev, { key: keyer.current++, text: "", original: null }])
          }
          data-testid="task-criterion-add"
        >
          {t("criterionAdd")}
        </Button>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setEditing(false)}
            data-testid="task-criteria-cancel"
          >
            {t("cancel")}
          </Button>
          {/* Se deshabilita Y se listan los motivos: un botón apagado sin
              explicación deja al operador sin saber qué le falta. */}
          <Button
            size="sm"
            onClick={() => mutation.mutate(cleanCriteria(rows))}
            disabled={mutation.isPending || blockingErrors.length > 0}
            data-testid="task-criteria-save"
          >
            {t("save")}
          </Button>
        </div>
      </div>
      {mutation.isError ? (
        <p className="text-destructive mt-1 text-sm">
          {t("criteriaSaveError")} {errorText(mutation.error)}
        </p>
      ) : null}
    </section>
  );
}

/**
 * La declaración que la fila traía de origen, para que «declarar» sobre un
 * criterio que ya venía con comando no lo pise con un formulario en blanco.
 */
function checkSeed(row: CriterionDraft): CriterionCheck | undefined {
  return draftFromCriterion(row.original).check ?? undefined;
}

/** Side-by-side "current vs proposed" confirmation shown before an AI
 * regeneration can replace criteria the task already had. Accepting funnels the
 * proposal into the editor (an explicit Save persists); cancelling keeps the
 * current criteria untouched. */
function CriteriaCompareDialog({
  open,
  current,
  proposed,
  onAccept,
  onCancel,
}: {
  open: boolean;
  current: unknown[];
  proposed: string[];
  onAccept: () => void;
  onCancel: () => void;
}) {
  const t = useT("taskDetail");
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
      size="lg"
    >
      <DialogContent data-testid="task-criteria-compare">
        <DialogHeader>
          <DialogTitle>{t("compareTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="grid grid-cols-2 gap-4">
            <div data-testid="task-criteria-compare-current">
              <h5 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
                {t("compareCurrent")}
              </h5>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {current.map((c, i) => (
                  <li key={i}>{criterionText(c)}</li>
                ))}
              </ul>
            </div>
            <div data-testid="task-criteria-compare-proposed">
              <h5 className="text-muted-foreground mb-1 text-xs font-semibold uppercase tracking-wide">
                {t("compareProposed")}
              </h5>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {proposed.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            data-testid="task-criteria-compare-cancel"
          >
            {t("cancel")}
          </Button>
          <Button size="sm" onClick={onAccept} data-testid="task-criteria-compare-accept">
            {t("compareAccept")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
