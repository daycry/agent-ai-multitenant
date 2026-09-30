"use client";

/**
 * Línea de tiempo de una memoria (`task_mem_20` sobre `GET /memories/{id}/timeline`): «qué
 * estaba pasando cuando se aprendió esto». Las ejecuciones vecinas, del mismo ámbito y dueño,
 * con sus memorias como índice; cada una enlaza a su ficha.
 */

import Link from "next/link";

import { useT } from "@/lib/i18n";

import { type MemoryTimeline } from "./types";

export function MemoryTimelineView({ timeline }: { timeline: MemoryTimeline | null }) {
  const t = useT("memoriesCard");
  const executions = timeline?.executions ?? [];

  return (
    <section data-testid="memory-timeline" className="space-y-2">
      <div>
        <h3 className="text-sm font-semibold">{t("timelineTitle")}</h3>
        <p className="text-muted-foreground text-xs">{t("timelineHint")}</p>
      </div>
      {executions.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{t("timelineEmpty")}</p>
      ) : (
        <ol className="space-y-2">
          {executions.map((ex, i) => (
            <li
              key={ex.execution_id ?? `manual-${i}`}
              className={
                "rounded border px-3 py-2 text-xs " + (ex.position === 0 ? "border-primary" : "")
              }
              data-position={ex.position}
            >
              <p className="text-muted-foreground mb-1 font-medium">
                {ex.execution_id === null
                  ? t("timelineManual")
                  : ex.position === 0
                    ? t("timelineNow")
                    : ex.position < 0
                      ? t("timelineBefore", { n: Math.abs(ex.position) })
                      : t("timelineAfter", { n: ex.position })}
                {ex.execution_id ? (
                  <Link
                    href={`/admin/executions/${ex.execution_id}`}
                    className="ml-2 font-mono underline"
                  >
                    {ex.execution_id.slice(0, 8)}
                  </Link>
                ) : null}
              </p>
              <ul className="space-y-1">
                {ex.memories.map((m) => (
                  <li key={m.id} data-testid={`memory-timeline-item-${m.id}`}>
                    <Link href={`/admin/memories/${m.id}`} className="hover:underline">
                      {m.headline}
                    </Link>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
