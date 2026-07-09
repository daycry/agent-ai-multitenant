"use client";

/**
 * Secciones PRESENTACIONALES del detalle de plan (hallazgo #9, refactor por partes).
 *
 * Componentes puros (solo props, sin hooks ni fetch) extraídos de `page.tsx`:
 * Gantt, DAG, Resumen (+ScopeList), Estimaciones (+EstimateCell), Fases y Tareas.
 * Movidos verbatim — cero cambio de comportamiento ni de testids. No es una ruta
 * (nombre ≠ page.tsx dentro de `app/**`); lleva 'use client' por consumir componentes
 * de cliente (Badge/Card/PlanDAG/PlanGantt).
 */

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PlanDAG } from "@/lib/plan-dag";
import { PlanGantt } from "@/lib/plan-gantt";
import { renderPlanDraft } from "@/lib/plan-draft-md";

import { type PlanSpecification, formatCostRange } from "./plan-spec-types";

// --------------------------------------------------------------------------
// Gantt visualisation with critical path (task_03_20)
// --------------------------------------------------------------------------
export function GanttSection({ tasks }: { tasks: PlanSpecification["tasks"] | undefined }) {
  if (!tasks || tasks.length === 0) return null;
  return (
    <Card className="mt-6" data-testid="plan-gantt">
      <CardHeader>
        <CardTitle>Gantt</CardTitle>
      </CardHeader>
      <CardContent>
        <PlanGantt tasks={tasks} />
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// DAG visualisation (task_03_19)
// --------------------------------------------------------------------------
export function DAGSection({ tasks }: { tasks: PlanSpecification["tasks"] | undefined }) {
  if (!tasks || tasks.length === 0) return null;
  return (
    <Card className="mt-6" data-testid="plan-dag">
      <CardHeader>
        <CardTitle>Grafo de dependencias</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <PlanDAG tasks={tasks} />
        </div>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Sections
// --------------------------------------------------------------------------
export function SummarySection({ summary }: { summary: PlanSpecification["summary"] | undefined }) {
  if (!summary || Object.keys(summary).length === 0) {
    return (
      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Resumen</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm italic" data-testid="plan-summary-empty">
            Este plan aún no tiene resumen. La sección se rellenará cuando el equipo termine la
            conversación de planning.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-6" data-testid="plan-summary">
      <CardHeader>
        <CardTitle>Resumen</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {summary.description ? <div>{renderPlanDraft(summary.description)}</div> : null}
        {summary.scope_in && summary.scope_in.length > 0 ? (
          <ScopeList label="En alcance" items={summary.scope_in} testId="plan-scope-in" />
        ) : null}
        {summary.scope_out && summary.scope_out.length > 0 ? (
          <ScopeList label="Fuera de alcance" items={summary.scope_out} testId="plan-scope-out" />
        ) : null}
        {summary.decisions && summary.decisions.length > 0 ? (
          <ScopeList label="Decisiones" items={summary.decisions} testId="plan-decisions" />
        ) : null}
        {summary.risks && summary.risks.length > 0 ? (
          <div data-testid="plan-risks">
            <p className="font-semibold">Riesgos</p>
            <ul className="list-disc pl-5">
              {summary.risks.map((risk, i) => {
                if (typeof risk === "string") return <li key={i}>{risk}</li>;
                return (
                  <li key={i}>
                    <span className="font-medium">{risk.name}</span>
                    {risk.mitigation ? (
                      <span className="text-muted-foreground"> — {risk.mitigation}</span>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ScopeList({ label, items, testId }: { label: string; items: string[]; testId: string }) {
  return (
    <div data-testid={testId}>
      <p className="font-semibold">{label}</p>
      <ul className="list-disc pl-5">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function EstimatesSection({
  estimates,
}: {
  estimates: PlanSpecification["estimates"] | undefined;
}) {
  if (!estimates || Object.keys(estimates).length === 0) return null;
  const humanCost = formatCostRange(estimates.cost_human_eur);
  const aiCost = formatCostRange(estimates.cost_ai_eur);

  return (
    <Card className="mt-6" data-testid="plan-estimates">
      <CardHeader>
        <CardTitle>Estimaciones</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <EstimateCell
            label="Duración"
            value={estimates.duration_calendar}
            testId="estimate-duration"
          />
          <EstimateCell
            label="Esfuerzo (persona-días)"
            value={
              estimates.effort_person_days !== undefined
                ? String(estimates.effort_person_days)
                : null
            }
            testId="estimate-effort"
          />
          <EstimateCell label="Coste humano" value={humanCost} testId="estimate-cost-human" />
          <EstimateCell label="Coste IA" value={aiCost} testId="estimate-cost-ai" />
        </dl>
      </CardContent>
    </Card>
  );
}

function EstimateCell({
  label,
  value,
  testId,
}: {
  label: string;
  value: string | null | undefined;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">{label}</dt>
      <dd className="font-medium">{value ?? "—"}</dd>
    </div>
  );
}

export function PhasesSection({
  phases,
  tasks,
}: {
  phases: PlanSpecification["phases"] | undefined;
  tasks: PlanSpecification["tasks"] | undefined;
}) {
  if (!phases || phases.length === 0) return null;
  const titleById = new Map<string, string>((tasks ?? []).map((t) => [t.id, t.title]));
  return (
    <Card className="mt-6" data-testid="plan-phases">
      <CardHeader>
        <CardTitle>Fases</CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="space-y-3 list-decimal pl-5">
          {phases.map((phase, i) => (
            <li key={i} data-testid={`plan-phase-${i}`}>
              <p className="font-medium">{phase.name}</p>
              {phase.description ? (
                <p className="text-muted-foreground text-xs">{phase.description}</p>
              ) : null}
              {phase.tasks && phase.tasks.length > 0 ? (
                <ul className="mt-1 text-xs list-disc pl-5">
                  {phase.tasks.map((tid) => (
                    <li key={tid}>
                      <span className="font-mono">{tid}</span>
                      {titleById.has(tid) ? (
                        <span className="text-muted-foreground"> · {titleById.get(tid)}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

export function TasksSection({ tasks }: { tasks: PlanSpecification["tasks"] | undefined }) {
  if (!tasks || tasks.length === 0) return null;
  return (
    <Card className="mt-6" data-testid="plan-tasks">
      <CardHeader>
        <CardTitle>Tareas ({tasks.length})</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left">
              <tr className="border-muted border-b">
                <th className="py-1 pr-2 font-semibold">ID</th>
                <th className="py-1 pr-2 font-semibold">Título</th>
                <th className="py-1 pr-2 font-semibold">Rol</th>
                <th className="py-1 pr-2 font-semibold">Compl.</th>
                <th className="py-1 pr-2 font-semibold">Depende de</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr
                  key={task.id}
                  className="border-muted/40 border-b align-top"
                  data-testid={`plan-task-${task.id}`}
                >
                  <td className="py-1 pr-2 font-mono">{task.id}</td>
                  <td className="py-1 pr-2">
                    {task.title}
                    {task.origin === "correction" ? (
                      <Badge
                        variant="warning"
                        className="ml-1.5"
                        data-testid={`plan-task-origin-${task.id}`}
                      >
                        corrección
                      </Badge>
                    ) : null}
                  </td>
                  <td className="py-1 pr-2">{task.role ?? "—"}</td>
                  <td className="py-1 pr-2">{task.complexity ?? "—"}</td>
                  <td className="py-1 pr-2 font-mono">
                    {task.depends_on && task.depends_on.length > 0
                      ? task.depends_on.join(", ")
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
