/**
 * Gantt view with critical path for a plan's task spec (Plan 03 task_03_20).
 *
 * Forward + backward pass over the DAG (Critical Path Method):
 *   - earliest_start[v] = max(earliest_end[u] for u in deps(v))
 *   - earliest_end[v]   = earliest_start[v] + duration[v]
 *   - latest_end[v]     = min(latest_start[w] for w in successors(v))
 *                         or project_end when v has no successors
 *   - latest_start[v]   = latest_end[v] - duration[v]
 *   - slack[v]          = latest_start[v] - earliest_start[v]
 *
 * A task is on the critical path iff slack == 0. The longest such
 * chain dominates the project duration; shortening any of its tasks
 * shortens the whole project.
 *
 * Render: one row per task, a bar from earliest_start to earliest_end
 * scaled to a fixed pixel-per-hour. Critical bars are amber; the rest
 * blue. A vertical guide at the project end caps the chart.
 */

import React, { useMemo } from "react";

import { cn } from "@/lib/utils";

export interface PlanGanttTask {
  id: string;
  title: string;
  estimated_hours?: number;
  depends_on?: string[];
}

interface ComputedTask {
  id: string;
  title: string;
  duration: number;
  earliest_start: number;
  earliest_end: number;
  latest_start: number;
  latest_end: number;
  slack: number;
  is_critical: boolean;
}

const DEFAULT_DURATION_HOURS = 1;
const PX_PER_HOUR = 24;
const ROW_HEIGHT = 28;
const ROW_GAP = 6;
const LABEL_WIDTH = 180;

/**
 * Compute earliest/latest start+end and slack for each task. Tasks
 * appearing in a cycle (caller should have validated before) are
 * skipped — the function tolerates the absence of cycles, and a
 * cycle would otherwise spin Kahn's algorithm.
 */
function computeSchedule(tasks: PlanGanttTask[]): ComputedTask[] {
  const idSet = new Set(tasks.map((t) => t.id));
  const duration: Map<string, number> = new Map(
    tasks.map((t) => [t.id, Math.max(t.estimated_hours ?? DEFAULT_DURATION_HOURS, 0.5)]),
  );
  const deps: Map<string, string[]> = new Map(
    tasks.map((t) => [t.id, (t.depends_on ?? []).filter((d) => idSet.has(d))]),
  );
  const successors: Map<string, string[]> = new Map(tasks.map((t) => [t.id, []]));
  for (const [child, parents] of deps) {
    for (const p of parents) successors.get(p)!.push(child);
  }

  // Forward pass — Kahn order.
  const indegree: Map<string, number> = new Map(tasks.map((t) => [t.id, deps.get(t.id)!.length]));
  const queue: string[] = [];
  for (const t of tasks) if ((indegree.get(t.id) ?? 0) === 0) queue.push(t.id);

  const earliestStart: Map<string, number> = new Map();
  const earliestEnd: Map<string, number> = new Map();
  for (const t of tasks) {
    earliestStart.set(t.id, 0);
    earliestEnd.set(t.id, 0);
  }
  const order: string[] = [];
  while (queue.length > 0) {
    const id = queue.shift()!;
    order.push(id);
    const start = Math.max(0, ...deps.get(id)!.map((d) => earliestEnd.get(d) ?? 0));
    earliestStart.set(id, start);
    earliestEnd.set(id, start + (duration.get(id) ?? DEFAULT_DURATION_HOURS));
    for (const s of successors.get(id) ?? []) {
      indegree.set(s, (indegree.get(s) ?? 0) - 1);
      if (indegree.get(s) === 0) queue.push(s);
    }
  }

  const projectEnd = Math.max(0, ...Array.from(earliestEnd.values()));

  // Backward pass.
  const latestEnd: Map<string, number> = new Map();
  const latestStart: Map<string, number> = new Map();
  for (const id of [...order].reverse()) {
    const succ = successors.get(id) ?? [];
    const end =
      succ.length === 0
        ? projectEnd
        : Math.min(...succ.map((s) => latestStart.get(s) ?? projectEnd));
    latestEnd.set(id, end);
    latestStart.set(id, end - (duration.get(id) ?? DEFAULT_DURATION_HOURS));
  }

  return tasks.map((t): ComputedTask => {
    const es = earliestStart.get(t.id) ?? 0;
    const ee = earliestEnd.get(t.id) ?? 0;
    const ls = latestStart.get(t.id) ?? 0;
    const le = latestEnd.get(t.id) ?? 0;
    const slack = Math.max(0, ls - es);
    return {
      id: t.id,
      title: t.title,
      duration: duration.get(t.id) ?? DEFAULT_DURATION_HOURS,
      earliest_start: es,
      earliest_end: ee,
      latest_start: ls,
      latest_end: le,
      slack,
      is_critical: slack < 0.001,
    };
  });
}

export function PlanGantt({ tasks }: { tasks: PlanGanttTask[] }) {
  const computed = useMemo(() => computeSchedule(tasks), [tasks]);

  if (computed.length === 0) {
    return (
      <p className="text-muted-foreground text-sm italic" data-testid="plan-gantt-empty">
        Sin tareas para representar.
      </p>
    );
  }

  const projectEnd = Math.max(0, ...computed.map((t) => t.earliest_end));
  const totalWidth = LABEL_WIDTH + projectEnd * PX_PER_HOUR + 24;
  const totalHeight = computed.length * (ROW_HEIGHT + ROW_GAP) + 16;

  return (
    <div className="overflow-x-auto">
      <svg
        data-testid="plan-gantt-svg"
        role="img"
        aria-label="Diagrama de Gantt con línea crítica"
        width={totalWidth}
        height={totalHeight}
        viewBox={`0 0 ${totalWidth} ${totalHeight}`}
        className="border-muted rounded border bg-background"
      >
        {/* Project-end guide */}
        <line
          x1={LABEL_WIDTH + projectEnd * PX_PER_HOUR}
          y1={0}
          x2={LABEL_WIDTH + projectEnd * PX_PER_HOUR}
          y2={totalHeight}
          stroke="currentColor"
          strokeOpacity={0.2}
          strokeDasharray="4 2"
          data-testid="plan-gantt-project-end"
        />

        {computed.map((task, idx) => {
          const y = 8 + idx * (ROW_HEIGHT + ROW_GAP);
          const barX = LABEL_WIDTH + task.earliest_start * PX_PER_HOUR;
          const barW = Math.max(task.duration * PX_PER_HOUR, 4);
          const fillClass = task.is_critical
            ? "fill-amber-500/30 stroke-amber-500"
            : "fill-indigo-500/20 stroke-indigo-500";

          return (
            <g
              key={task.id}
              data-testid={`plan-gantt-row-${task.id}`}
              data-critical={task.is_critical ? "true" : "false"}
              data-earliest-start={task.earliest_start}
              data-earliest-end={task.earliest_end}
              data-slack={task.slack}
            >
              <text
                x={8}
                y={y + ROW_HEIGHT / 2 + 4}
                className="fill-foreground text-[10px] font-mono"
              >
                {task.id}
              </text>
              <text
                x={LABEL_WIDTH - 8}
                y={y + ROW_HEIGHT / 2 + 4}
                textAnchor="end"
                className="fill-muted-foreground text-[10px]"
              >
                {task.duration}h
              </text>
              <rect
                x={barX}
                y={y}
                width={barW}
                height={ROW_HEIGHT}
                rx={4}
                ry={4}
                className={cn(fillClass)}
                strokeWidth={1.5}
                data-testid={`plan-gantt-bar-${task.id}`}
              />
            </g>
          );
        })}
      </svg>
      <p className="text-muted-foreground mt-2 text-xs" data-testid="plan-gantt-summary">
        Duración total estimada: <strong>{projectEnd}h</strong>. Las tareas en ámbar forman la línea
        crítica.
      </p>
    </div>
  );
}
