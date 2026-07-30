"use client";

/**
 * Editor de la especificación del plan antes de aprobarlo (`task_wf_42`).
 *
 * Hasta ahora el spec era de SOLO LECTURA en toda la UI: si el equipo de
 * planning planteaba mal una tarea, o le colgaba una dependencia equivocada, la
 * única salida era rechazar el plan y volver a chatear. Editarla aquí es el
 * gesto pequeño que faltaba entre «el plan casi está» y «apruébalo».
 *
 * Envuelve a `TasksSection` en vez de duplicar su tabla: en reposo se ve
 * exactamente lo de siempre, y el botón «Editar tareas» cambia esa misma
 * sección por el formulario. Solo aparece en `draft`/`pending_approval`; el
 * backend lo vuelve a comprobar (409 `spec_not_editable`).
 *
 * El guardado manda el spec COMPLETO, no solo `tasks`: `PlanSpecification` en
 * el backend tiene defaults por campo, así que un PUT parcial borraría el
 * sumario, las fases y las estimaciones sin decir nada.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { apiFetch } from "@/lib/api";
import {
  describeSaveError,
  localSpecProblems,
  nextTaskId,
  removeTask,
  specEditable,
  toDrafts,
  toTaskSpecs,
  type TaskDraft,
} from "@/lib/plan-spec-edit";
import { TasksSection } from "./plan-spec-sections";
import type { PlanResponse, PlanSpecification } from "./plan-spec-types";

export function PlanSpecEditorSection({
  planId,
  status,
  spec,
}: {
  planId: string;
  status: string;
  spec: PlanSpecification;
}) {
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<TaskDraft[] | null>(null);
  const [keyer, setKeyer] = useState(0);

  const mutation = useMutation({
    mutationFn: (tasks: TaskDraft[]) =>
      apiFetch<PlanResponse>(`/plans/${planId}`, {
        method: "PUT",
        // El spec ENTERO: un PUT parcial pierde summary/phases/estimates.
        body: { specification: { ...spec, tasks: toTaskSpecs(tasks) } },
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["plan", planId], updated);
      setDrafts(null);
    },
  });

  function startEdit() {
    const initial = toDrafts(spec.tasks ?? []);
    setKeyer(initial.length);
    setDrafts(initial);
    mutation.reset();
  }

  if (drafts === null) {
    return (
      <>
        <TasksSection tasks={spec.tasks} />
        {specEditable(status) ? (
          <div className="mt-2 flex justify-end">
            <Button
              variant="outline"
              size="sm"
              onClick={startEdit}
              data-testid="plan-spec-edit-open"
            >
              <Pencil className="mr-1 h-3.5 w-3.5" />
              Editar tareas
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  const problems = localSpecProblems(drafts);
  const serverError = mutation.isError ? describeSaveError(mutation.error, drafts) : null;

  function patch(key: number, change: Partial<TaskDraft>) {
    setDrafts((prev) => (prev ?? []).map((d) => (d.key === key ? { ...d, ...change } : d)));
  }

  function addTask() {
    setDrafts((prev) => {
      const rows = prev ?? [];
      return [
        ...rows,
        {
          key: keyer,
          id: nextTaskId(rows),
          title: "",
          description: "",
          role: "",
          complexity: "",
          estimatedHours: "",
          dependsOn: [],
          criteria: "",
          rest: {},
        },
      ];
    });
    setKeyer((k) => k + 1);
  }

  return (
    <Card className="mt-6" data-testid="plan-spec-editor">
      <CardHeader>
        <CardTitle>Editar tareas ({drafts.length})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {drafts.map((draft) => (
          <TaskDraftRow
            key={draft.key}
            draft={draft}
            others={drafts.filter((d) => d.key !== draft.key)}
            onChange={(change) => patch(draft.key, change)}
            onRemove={() => setDrafts((prev) => removeTask(prev ?? [], draft.key))}
          />
        ))}

        {drafts.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="plan-spec-editor-empty">
            El plan no tiene tareas. Añade la primera.
          </p>
        ) : null}

        <Button variant="outline" size="sm" onClick={addTask} data-testid="plan-spec-add-task">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Añadir tarea
        </Button>

        {problems.length > 0 ? (
          <ul
            className="bg-warning-soft text-warning-soft-foreground list-disc space-y-0.5 rounded p-3 pl-7 text-xs"
            data-testid="plan-spec-problems"
          >
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}

        {serverError ? (
          <p
            className="bg-danger-soft text-danger-soft-foreground rounded p-3 text-sm"
            data-testid="plan-spec-save-error"
          >
            {serverError}
          </p>
        ) : null}

        <div className="flex justify-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setDrafts(null)}
            data-testid="plan-spec-cancel"
          >
            Cancelar
          </Button>
          <Button
            size="sm"
            disabled={problems.length > 0 || mutation.isPending}
            onClick={() => mutation.mutate(drafts)}
            data-testid="plan-spec-save"
          >
            {mutation.isPending ? "Guardando…" : "Guardar cambios"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function TaskDraftRow({
  draft,
  others,
  onChange,
  onRemove,
}: {
  draft: TaskDraft;
  others: readonly TaskDraft[];
  onChange: (change: Partial<TaskDraft>) => void;
  onRemove: () => void;
}) {
  function toggleDependency(id: string) {
    onChange({
      dependsOn: draft.dependsOn.includes(id)
        ? draft.dependsOn.filter((d) => d !== id)
        : [...draft.dependsOn, id],
    });
  }

  return (
    <div
      className="border-muted space-y-3 rounded-md border p-3"
      data-testid={`plan-spec-row-${draft.id}`}
    >
      <div className="flex items-start gap-2">
        <div className="flex w-24 shrink-0 flex-col gap-1.5">
          <Label>ID</Label>
          <Input
            value={draft.id}
            onChange={(e) => onChange({ id: e.target.value })}
            className="font-mono text-xs"
            data-testid={`plan-spec-id-${draft.key}`}
          />
        </div>
        <div className="flex flex-1 flex-col gap-1.5">
          <Label>Título</Label>
          <Input
            value={draft.title}
            onChange={(e) => onChange({ title: e.target.value })}
            data-testid={`plan-spec-title-${draft.key}`}
          />
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRemove}
          aria-label={`Quitar la tarea ${draft.id}`}
          data-testid={`plan-spec-remove-${draft.key}`}
          className="mt-6"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label>Descripción</Label>
        <MarkdownTextarea
          value={draft.description}
          onChange={(next) => onChange({ description: next })}
          rows={3}
          data-testid={`plan-spec-description-${draft.key}`}
        />
      </div>

      <div className="grid grid-cols-3 gap-2">
        <div className="flex flex-col gap-1.5">
          <Label>Rol</Label>
          <Input
            value={draft.role}
            onChange={(e) => onChange({ role: e.target.value })}
            placeholder="backend_dev"
            data-testid={`plan-spec-role-${draft.key}`}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Complejidad</Label>
          <Input
            value={draft.complexity}
            onChange={(e) => onChange({ complexity: e.target.value })}
            placeholder="media"
            data-testid={`plan-spec-complexity-${draft.key}`}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Horas estimadas</Label>
          <Input
            value={draft.estimatedHours}
            onChange={(e) => onChange({ estimatedHours: e.target.value })}
            inputMode="decimal"
            placeholder="4"
            data-testid={`plan-spec-hours-${draft.key}`}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label>Criterios de aceptación (uno por línea)</Label>
        <MarkdownTextarea
          value={draft.criteria}
          onChange={(next) => onChange({ criteria: next })}
          rows={3}
          hint={null}
          data-testid={`plan-spec-criteria-${draft.key}`}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label>Depende de</Label>
        {others.length === 0 ? (
          <p className="text-muted-foreground text-xs italic">No hay otras tareas en el plan.</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {others.map((other) => {
              const active = draft.dependsOn.includes(other.id);
              return (
                <button
                  key={other.key}
                  type="button"
                  onClick={() => toggleDependency(other.id)}
                  aria-pressed={active}
                  title={other.title || other.id}
                  data-testid={`plan-spec-dep-${draft.key}-${other.id}`}
                  className={
                    active
                      ? "bg-primary text-primary-foreground rounded px-2 py-0.5 font-mono text-[11px]"
                      : "border-muted text-muted-foreground hover:text-foreground rounded border px-2 py-0.5 font-mono text-[11px]"
                  }
                >
                  {other.id}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
