/**
 * SVG DAG renderer for a plan's task graph (Plan 03 task_03_19).
 *
 * Pure-React SVG. Layout by depth columns: each task lands in
 * column = longest path from a root, row = index inside that column.
 * Nodes are simple rectangles; edges connect them with straight lines.
 *
 * Why not D3 / react-flow: the plan-detail page already pays the cost
 * of a few component trees, and a DAG of a few dozen tasks doesn't
 * need a layout engine — the deterministic depth-based placement is
 * good enough and ships zero extra deps.
 */

import React, { useMemo } from "react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface PlanDAGTask {
  id: string;
  title: string;
  depends_on?: string[];
}

interface LayoutNode {
  id: string;
  title: string;
  x: number;
  y: number;
  col: number;
  row: number;
}

interface LayoutEdge {
  from: string;
  to: string;
}

interface PlanDAGProps {
  tasks: PlanDAGTask[];
  // Test helpers (optional override of the defaults).
  nodeWidth?: number;
  nodeHeight?: number;
  colGap?: number;
  rowGap?: number;
}

const DEFAULT_NODE_W = 140;
const DEFAULT_NODE_H = 36;
const DEFAULT_COL_GAP = 60;
const DEFAULT_ROW_GAP = 24;

/**
 * Topologically order the tasks and compute the depth of each node
 * (longest path from a root, where a root has no incoming edges).
 *
 * Returns the depth map and the topological order.
 */
function computeDepths(tasks: PlanDAGTask[]): { depth: Map<string, number>; order: string[] } {
  const idSet = new Set(tasks.map((t) => t.id));
  const incoming: Map<string, string[]> = new Map();
  const indegree: Map<string, number> = new Map();
  for (const t of tasks) {
    incoming.set(t.id, []);
    indegree.set(t.id, 0);
  }
  for (const t of tasks) {
    for (const dep of t.depends_on ?? []) {
      if (!idSet.has(dep)) continue; // unknown dep — ignored
      incoming.get(t.id)!.push(dep);
      indegree.set(t.id, (indegree.get(t.id) ?? 0) + 1);
    }
  }
  // Children adjacency (reverse) for Kahn's algorithm.
  const children: Map<string, string[]> = new Map();
  for (const t of tasks) children.set(t.id, []);
  for (const [id, deps] of incoming) {
    for (const dep of deps) children.get(dep)!.push(id);
  }

  const queue: string[] = [];
  for (const t of tasks) if ((indegree.get(t.id) ?? 0) === 0) queue.push(t.id);

  const order: string[] = [];
  const depth: Map<string, number> = new Map();
  for (const r of queue) depth.set(r, 0);

  while (queue.length > 0) {
    const id = queue.shift()!;
    order.push(id);
    const myDepth = depth.get(id) ?? 0;
    for (const child of children.get(id) ?? []) {
      depth.set(child, Math.max(depth.get(child) ?? 0, myDepth + 1));
      indegree.set(child, (indegree.get(child) ?? 0) - 1);
      if (indegree.get(child) === 0) queue.push(child);
    }
  }
  return { depth, order };
}

export function PlanDAG({
  tasks,
  nodeWidth = DEFAULT_NODE_W,
  nodeHeight = DEFAULT_NODE_H,
  colGap = DEFAULT_COL_GAP,
  rowGap = DEFAULT_ROW_GAP,
}: PlanDAGProps) {
  const t = useT("planDetail");
  const layout = useMemo(() => {
    if (tasks.length === 0) {
      return { nodes: [] as LayoutNode[], edges: [] as LayoutEdge[], width: 0, height: 0 };
    }
    const { depth } = computeDepths(tasks);
    // Group ids by column.
    const byCol: Map<number, string[]> = new Map();
    for (const t of tasks) {
      const c = depth.get(t.id) ?? 0;
      const bucket = byCol.get(c) ?? [];
      bucket.push(t.id);
      byCol.set(c, bucket);
    }
    const titleById = new Map(tasks.map((t) => [t.id, t.title]));

    const nodes: LayoutNode[] = [];
    let maxCol = 0;
    let maxRowsInAnyCol = 0;
    for (const [col, ids] of byCol) {
      if (col > maxCol) maxCol = col;
      if (ids.length > maxRowsInAnyCol) maxRowsInAnyCol = ids.length;
      ids.sort();
      ids.forEach((id, row) => {
        nodes.push({
          id,
          title: titleById.get(id) ?? id,
          col,
          row,
          x: col * (nodeWidth + colGap),
          y: row * (nodeHeight + rowGap),
        });
      });
    }

    const edges: LayoutEdge[] = [];
    for (const t of tasks) {
      for (const dep of t.depends_on ?? []) {
        if (titleById.has(dep)) edges.push({ from: dep, to: t.id });
      }
    }

    const width = (maxCol + 1) * (nodeWidth + colGap);
    const height = maxRowsInAnyCol * (nodeHeight + rowGap);
    return { nodes, edges, width, height };
  }, [tasks, nodeWidth, nodeHeight, colGap, rowGap]);

  if (tasks.length === 0) {
    return (
      <p className="text-muted-foreground text-sm italic" data-testid="plan-dag-empty">
        {t("diagramEmpty")}
      </p>
    );
  }

  const nodeById = new Map(layout.nodes.map((n) => [n.id, n]));

  return (
    <svg
      data-testid="plan-dag-svg"
      role="img"
      aria-label={t("dagAriaLabel")}
      width={Math.max(layout.width, 200)}
      height={Math.max(layout.height + 8, 60)}
      viewBox={`0 0 ${Math.max(layout.width, 200)} ${Math.max(layout.height + 8, 60)}`}
      className="border-muted rounded border bg-background"
    >
      <defs>
        <marker
          id="dag-arrow"
          markerWidth="10"
          markerHeight="10"
          refX="9"
          refY="5"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
        </marker>
      </defs>
      {/* edges first so they render behind the nodes */}
      <g className="text-indigo-500" data-testid="plan-dag-edges">
        {layout.edges.map((e) => {
          const a = nodeById.get(e.from);
          const b = nodeById.get(e.to);
          if (!a || !b) return null;
          const x1 = a.x + nodeWidth;
          const y1 = a.y + nodeHeight / 2;
          const x2 = b.x;
          const y2 = b.y + nodeHeight / 2;
          return (
            <line
              key={`${e.from}->${e.to}`}
              data-testid={`plan-dag-edge-${e.from}->${e.to}`}
              x1={x1}
              y1={y1}
              x2={x2}
              y2={y2}
              stroke="currentColor"
              strokeWidth={1.5}
              markerEnd="url(#dag-arrow)"
            />
          );
        })}
      </g>
      <g data-testid="plan-dag-nodes">
        {layout.nodes.map((n) => (
          <g
            key={n.id}
            data-testid={`plan-dag-node-${n.id}`}
            data-col={n.col}
            data-row={n.row}
            transform={`translate(${n.x}, ${n.y})`}
          >
            <rect
              width={nodeWidth}
              height={nodeHeight}
              rx={6}
              ry={6}
              className={cn("fill-indigo-500/10 stroke-indigo-500")}
              strokeWidth={1}
            />
            <text
              x={nodeWidth / 2}
              y={nodeHeight / 2 + 4}
              textAnchor="middle"
              className="fill-foreground text-[10px] font-mono"
            >
              {n.id}
            </text>
          </g>
        ))}
      </g>
    </svg>
  );
}
