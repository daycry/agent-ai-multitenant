"use client";

/**
 * Cifras de una base para su tarjeta (`task_ui_21`): documentos, estado de
 * indexado y última actualización. Vienen AGREGADAS en el listado de bases
 * (calculadas en SQL sobre todos sus documentos): no hay petición por fila ni se
 * pierde un fallo que no cabía en la primera página.
 */

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { useT } from "@/lib/i18n";

import { summarizeStats, type IndexingState, type KbDocumentStats } from "./kb-types";

const STATE_VARIANT: Record<IndexingState, BadgeVariant> = {
  empty: "muted",
  indexed: "success",
  indexing: "warning",
  failed: "danger",
};

const STATE_KEY = {
  empty: "stateEmpty",
  indexed: "stateIndexed",
  indexing: "stateIndexing",
  failed: "stateFailed",
} as const;

export function KbStats({ kbId, stats }: { kbId: string; stats?: KbDocumentStats | null }) {
  const t = useT("knowledgeWizard");
  const summary = summarizeStats(stats);
  const date = summary.lastUpdate ? new Date(summary.lastUpdate).toLocaleDateString() : null;

  return (
    <div
      className="mt-1 flex flex-wrap items-center gap-2 text-xs"
      data-testid={`kb-stats-${kbId}`}
    >
      <span data-testid={`kb-stats-count-${kbId}`}>
        {t("documentsCount", { n: summary.count })}
      </span>
      <Badge variant={STATE_VARIANT[summary.state]} data-testid={`kb-stats-state-${kbId}`}>
        {t(STATE_KEY[summary.state])}
      </Badge>
      {date ? (
        <span className="text-muted-foreground" data-testid={`kb-stats-updated-${kbId}`}>
          {t("updatedOn", { date })}
        </span>
      ) : null}
    </div>
  );
}
