"use client";

/**
 * Cifras de una base para su tarjeta (`task_ui_21`): documentos, estado de
 * indexado y última actualización. Comparte la caché `["kb-documents", id]` con
 * la lista de documentos de la propia tarjeta, así que no cuesta una petición más.
 */

import { useQuery } from "@tanstack/react-query";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

import { summarizeDocuments, type IndexingState, type KbDocument } from "./kb-types";

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

export function KbStats({ kbId }: { kbId: string }) {
  const t = useT("knowledgeWizard");
  const docsQuery = useQuery({
    queryKey: ["kb-documents", kbId],
    queryFn: () => apiFetch<KbDocument[]>(`/knowledge-bases/${kbId}/documents`),
    refetchOnWindowFocus: false,
  });
  const summary = summarizeDocuments(docsQuery.data ?? []);
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
