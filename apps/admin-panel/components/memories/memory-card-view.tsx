"use client";

/**
 * La ficha de una memoria (`task_mem_20`, `GET /memories/{id}`): contenido, procedencia resuelta
 * (agente, proyecto, título de la ejecución), contadores de uso y utilidad, el voto humano y la
 * línea de tiempo. Una memoria inexistente, de otro tenant o privada de otra persona es un 404
 * indistinguible, y aquí se dice «no es visible» sin más.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { invalidateMemoryViews } from "@/lib/memory/invalidate";
import { renderPlanDraft } from "@/lib/plan-draft-md";

import { MemoryFeedbackButtons } from "./memory-feedback-buttons";
import { MemoryProvenance } from "./memory-provenance";
import { MemoryTimelineView } from "./memory-timeline";
import { MemoryUsage } from "./memory-usage";
import {
  SCOPE_KEY,
  SCOPE_VARIANT,
  TYPE_KEY,
  TYPE_VARIANT,
  kindKeyOf,
  type MemoryCardData,
} from "./types";

export function MemoryCardView({ id }: { id: string }) {
  const t = useT("memories");
  const tc = useT("memoriesCard");
  const tCommon = useT("common");
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["memories", "card", id],
    queryFn: () => apiFetch<MemoryCardData>(`/memories/${id}`),
    refetchOnWindowFocus: false,
    retry: false,
  });
  const refresh = () => invalidateMemoryViews(queryClient, id);

  if (query.isLoading) {
    return <p className="text-muted-foreground text-sm">{tCommon("loading")}</p>;
  }
  const m = query.data;
  if (query.isError || !m) {
    return (
      <p className="text-muted-foreground text-sm italic" data-testid="memory-card-error">
        {tc("notVisible")}
      </p>
    );
  }

  return (
    <Card data-testid={`memory-card-${m.id}`}>
      <CardContent className="space-y-4 pt-6">
        <div className="flex flex-wrap items-center gap-1 text-[10px] uppercase tracking-wide">
          <Badge variant={SCOPE_VARIANT[m.scope] ?? "muted"}>{t(SCOPE_KEY[m.scope])}</Badge>
          <Badge variant={TYPE_VARIANT[m.type] ?? "muted"}>{t(TYPE_KEY[m.type])}</Badge>
          <Badge variant={m.kind ? "primary" : "muted"}>{t(kindKeyOf(m.kind))}</Badge>
          {m.disputed ? (
            <Badge variant="danger" data-testid="memory-disputed-badge">
              {tc("disputedBadge")}
            </Badge>
          ) : null}
          {m.archived_at ? <Badge variant="muted">{tc("archivedBadge")}</Badge> : null}
          {m.tags.length > 0 ? (
            <span className="text-muted-foreground ml-1 font-mono">{m.tags.join(" · ")}</span>
          ) : null}
        </div>
        <div className="text-sm">{renderPlanDraft(m.content)}</div>
        <MemoryProvenance memory={m} executionTitle={m.execution_title} />
        <MemoryUsage memory={m} />
        <div className="space-y-1">
          <h3 className="text-sm font-semibold">{tc("feedbackTitle")}</h3>
          <MemoryFeedbackButtons memoryId={m.id} current={m.my_verdict} onVoted={refresh} />
          <p className="text-muted-foreground text-xs">{tc("feedbackHint")}</p>
        </div>
        <MemoryTimelineView timeline={m.timeline} />
      </CardContent>
    </Card>
  );
}
