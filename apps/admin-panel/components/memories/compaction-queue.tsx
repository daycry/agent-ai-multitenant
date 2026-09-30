"use client";

/**
 * La cola de revisiones de compactación (`task_mem_20`, backend de `task_mem_11`), sólo para el
 * `tenant_admin`: el job propone agrupar por proyecto las memorias disputadas y aquí el operador
 * confirma (se archivan con motivo) o rechaza (quedan como están). Nunca se borra nada.
 */

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { useErrorText } from "@/lib/use-error-text";

import { type Compaction, type MemoryResponse } from "./types";

function CompactionMemory({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["memories", "card-brief", id],
    queryFn: () => apiFetch<MemoryResponse>(`/memories/${id}`),
    staleTime: 60_000,
    retry: false,
  });
  return (
    <li className="truncate" data-testid={`compaction-memory-${id}`}>
      <Link href={`/admin/memories/${id}`} className="hover:underline">
        <span className="text-muted-foreground mr-1 font-mono">{id.slice(0, 8)}</span>
        {query.data?.content.slice(0, 120) ?? ""}
      </Link>
    </li>
  );
}

function CompactionItem({ proposal }: { proposal: Compaction }) {
  const t = useT("memoriesCard");
  const lang = useLangOptional();
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const decide = useMutation({
    mutationFn: (action: "confirm" | "reject") =>
      apiFetch(`/memories/compactions/${proposal.id}/${action}`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memories"] }),
  });

  return (
    <li className="rounded border px-3 py-2 text-sm" data-testid={`compaction-${proposal.id}`}>
      <p className="font-medium">
        {t("compactionProject")}: {proposal.project_name ?? proposal.project_id.slice(0, 8)} ·{" "}
        {t("compactionCount", { count: proposal.memory_ids.length })}
      </p>
      <p className="text-muted-foreground text-xs">
        {new Date(proposal.created_at).toLocaleString(lang === "en" ? "en-GB" : "es-ES")}
        {proposal.summary ? ` · ${proposal.summary}` : ""}
      </p>
      <ul className="mt-2 space-y-0.5 text-xs">
        {proposal.memory_ids.map((mid) => (
          <CompactionMemory key={mid} id={mid} />
        ))}
      </ul>
      <div className="mt-2 flex gap-2">
        <Button
          size="sm"
          disabled={decide.isPending}
          onClick={() => decide.mutate("confirm")}
          data-testid={`compaction-confirm-${proposal.id}`}
        >
          {t("compactionConfirm")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={decide.isPending}
          onClick={() => decide.mutate("reject")}
          data-testid={`compaction-reject-${proposal.id}`}
        >
          {t("compactionReject")}
        </Button>
      </div>
      {decide.isError ? (
        <p className="text-destructive mt-1 text-xs">{errorText(decide.error)}</p>
      ) : null}
    </li>
  );
}

export function CompactionQueue() {
  const t = useT("memoriesCard");
  const errorText = useErrorText();
  const query = useQuery({
    queryKey: ["memories", "compactions"],
    queryFn: () => apiFetch<Compaction[]>("/memories/compactions?status=pending"),
    refetchOnWindowFocus: false,
    retry: false,
  });
  const proposals = Array.isArray(query.data) ? query.data : [];

  return (
    <Card className="mt-6" data-testid="compactions-card">
      <CardHeader>
        <CardTitle>
          {t("compactionTitle")} ({proposals.length})
        </CardTitle>
        <p className="text-muted-foreground text-xs">{t("compactionHint")}</p>
      </CardHeader>
      <CardContent>
        {query.isError ? (
          <p className="text-destructive text-sm">{errorText(query.error)}</p>
        ) : proposals.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="compactions-empty">
            {t("compactionEmpty")}
          </p>
        ) : (
          <ul className="space-y-2">
            {proposals.map((p) => (
              <CompactionItem key={p.id} proposal={p} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
