"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { memoryDetectorState } from "@/lib/memory/honesty";
import { renderPlanDraft } from "@/lib/plan-draft-md";

import { MemoryProvenance } from "./memory-provenance";
import { SimilarMemoriesDialog, type SimilarItem } from "./similar-memories-dialog";
import {
  SCOPE_KEY,
  SCOPE_VARIANT,
  TYPE_KEY,
  TYPE_VARIANT,
  kindKeyOf,
  type MemoryResponse,
} from "./types";

// --------------------------------------------------------------------------
// List
// --------------------------------------------------------------------------
export function MemoryList({
  memories,
  onDeleted,
}: {
  memories: MemoryResponse[];
  onDeleted: () => void;
}) {
  return (
    <ul className="space-y-2" data-testid="memories-list">
      {memories.map((m) => (
        <MemoryRow key={m.id} memory={m} onDeleted={onDeleted} />
      ))}
    </ul>
  );
}

function MemoryRow({ memory, onDeleted }: { memory: MemoryResponse; onDeleted: () => void }) {
  const t = useT("memories");
  const [similarOpen, setSimilarOpen] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => apiFetch<void>(`/memories/${memory.id}`, { method: "DELETE" }),
    onSuccess: () => onDeleted(),
  });

  return (
    <li
      className="border-muted flex items-start justify-between gap-3 rounded border px-3 py-2 text-sm"
      data-testid={`memory-${memory.id}`}
      data-scope={memory.scope}
      data-type={memory.type}
      data-kind={memory.kind ?? "unclassified"}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1 text-[10px] uppercase tracking-wide">
          <Badge variant={SCOPE_VARIANT[memory.scope] ?? "muted"}>
            {t(SCOPE_KEY[memory.scope])}
          </Badge>
          <Badge variant={TYPE_VARIANT[memory.type] ?? "muted"}>{t(TYPE_KEY[memory.type])}</Badge>
          <Badge
            variant={memory.kind ? "primary" : "muted"}
            data-testid={`memory-kind-${memory.id}`}
          >
            {t(kindKeyOf(memory.kind))}
          </Badge>
          {memory.has_embedding ? (
            <Badge variant="success">embedding</Badge>
          ) : (
            <SimilarUnavailableBadge memoryId={memory.id} />
          )}
          <SimilarCountBadge
            memoryId={memory.id}
            hasEmbedding={memory.has_embedding}
            onClick={() => setSimilarOpen(true)}
          />
          {memory.tags.length > 0 ? (
            <span className="text-muted-foreground ml-1 font-mono">{memory.tags.join(" · ")}</span>
          ) : null}
        </div>
        <div className="mt-1 text-sm">{renderPlanDraft(memory.content)}</div>
        <MemoryProvenance memory={memory} />
      </div>
      <div className="flex gap-1">
        <Button
          variant="outline"
          size="sm"
          onClick={() => deleteMutation.mutate()}
          disabled={deleteMutation.isPending}
          data-testid={`memory-delete-${memory.id}`}
          aria-label={t("delete")}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      {similarOpen && (
        <SimilarMemoriesDialog
          memory={memory}
          onOpenChange={setSimilarOpen}
          onChanged={onDeleted}
        />
      )}
    </li>
  );
}

// --------------------------------------------------------------------------
// Similar count badge — pre-fetches /similar para cada memoria con
// embedding y muestra "N similar/es" (amarillo, clickable) sólo si
// N > 0. Si no hay candidatos sobre el umbral, no se renderiza nada.
//
// Coste: una query /similar por memoria con embedding. Para tenants
// con muchas memorias conviene un endpoint batched
// `/memories/similar-counts` (follow-up); para volúmenes normales el
// caché de TanStack (staleTime 30s) lo hace barato.
// --------------------------------------------------------------------------

// --------------------------------------------------------------------------
// Honestidad de estado (Plan 06.17 task_06_17_06): una memoria SIN embedding
// no puede tener "similares" (cos-sim sobre un vector ausente) ni participar
// del umbral. En vez de no mostrar nada (que parecía "0 similares, todo bien"),
// se marca "No disponible aún" con el motivo en el tooltip accesible.
// --------------------------------------------------------------------------
function SimilarUnavailableBadge({ memoryId }: { memoryId: string }) {
  const { lang } = useLang();
  const state = memoryDetectorState(false, lang);
  return (
    <Tooltip content={state.note}>
      <TooltipTrigger>
        <Badge variant="muted" data-testid={`memory-similar-unavailable-${memoryId}`}>
          {state.label}
        </Badge>
      </TooltipTrigger>
    </Tooltip>
  );
}

function SimilarCountBadge({
  memoryId,
  hasEmbedding,
  onClick,
}: {
  memoryId: string;
  hasEmbedding: boolean;
  onClick: () => void;
}) {
  const t = useT("memories");
  const query = useQuery<SimilarItem[], ApiError>({
    queryKey: ["memory-similar", memoryId],
    queryFn: () => apiFetch<SimilarItem[]>(`/memories/${memoryId}/similar`),
    enabled: hasEmbedding,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    retry: false,
  });
  const count = query.data?.length ?? 0;
  if (!hasEmbedding || count === 0) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`memory-similar-badge-${memoryId}`}
      className="bg-warning-soft text-warning-soft-foreground hover:bg-warning-soft/80 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide transition-colors"
      aria-label={t("similarAria", { count })}
    >
      {count === 1 ? t("similarBadgeOne") : t("similarBadgeMany", { count })}
    </button>
  );
}
