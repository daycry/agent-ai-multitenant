"use client";

/**
 * Voto humano sobre una memoria (`task_mem_20`, `human_mem_02`): útil, errónea o desfasada.
 *
 * Un voto por persona; repetirlo no suma y cambiarlo lo reescribe. «Errónea» y «desfasada»
 * dejan la memoria disputada y el recall la baja, sin borrarla (`task_mem_10`).
 */

import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

import { type MemoriesCardKey, type MemoryVerdict } from "./types";

const VERDICTS: { verdict: MemoryVerdict; key: MemoriesCardKey }[] = [
  { verdict: "useful", key: "verdictUseful" },
  { verdict: "wrong", key: "verdictWrong" },
  { verdict: "outdated", key: "verdictOutdated" },
];

export function MemoryFeedbackButtons({
  memoryId,
  current,
  onVoted,
}: {
  memoryId: string;
  current?: MemoryVerdict | null;
  onVoted: () => void;
}) {
  const t = useT("memoriesCard");
  const vote = useMutation({
    mutationFn: (verdict: MemoryVerdict) =>
      apiFetch(`/memories/${memoryId}/feedback`, { method: "POST", body: { verdict } }),
    onSuccess: () => onVoted(),
  });

  return (
    <div
      className="flex flex-wrap items-center gap-1"
      role="group"
      aria-label={t("feedbackTitle")}
      data-testid={`memory-feedback-${memoryId}`}
    >
      {VERDICTS.map(({ verdict, key }) => (
        <Button
          key={verdict}
          type="button"
          size="sm"
          variant={current === verdict ? "default" : "outline"}
          aria-pressed={current === verdict}
          disabled={vote.isPending}
          onClick={() => vote.mutate(verdict)}
          data-testid={`memory-feedback-${verdict}-${memoryId}`}
        >
          {t(key)}
        </Button>
      ))}
    </div>
  );
}
