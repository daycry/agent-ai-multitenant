"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitMerge, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { invalidateMemoryViews } from "@/lib/memory/invalidate";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { useErrorText } from "@/lib/use-error-text";

import { type MemoryResponse } from "./types";

// Similar memories dialog (Plan 06.7 task_06_7_08)
export interface SimilarItem {
  memory: MemoryResponse;
  similarity: number;
}

export function SimilarMemoriesDialog({
  memory,
  onOpenChange,
  onChanged,
}: {
  memory: MemoryResponse;
  onOpenChange: (v: boolean) => void;
  onChanged: () => void;
}) {
  const t = useT("memories");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const similarQuery = useQuery<SimilarItem[], ApiError>({
    queryKey: ["memory-similar", memory.id],
    queryFn: () => apiFetch<SimilarItem[]>(`/memories/${memory.id}/similar`),
    refetchOnWindowFocus: false,
  });

  const refetchAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["memory-similar", memory.id] });
    invalidateMemoryViews(queryClient);
    onChanged();
  };

  const mergeMutation = useMutation<MemoryResponse, ApiError, string>({
    mutationFn: (sourceId: string) =>
      apiFetch<MemoryResponse>(`/memories/${sourceId}/merge-into`, {
        method: "POST",
        body: { target_id: memory.id },
      }),
    onSuccess: () => refetchAll(),
  });

  const discardMutation = useMutation<void, ApiError, string>({
    mutationFn: (candidateId: string) =>
      apiFetch<void>(`/memories/${candidateId}`, { method: "DELETE" }),
    onSuccess: () => refetchAll(),
  });

  return (
    <Dialog open={true} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("similarTitle")}</DialogTitle>
          <DialogDescription>{t("similarDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <Card className="bg-muted/40 p-3">
            <p className="text-muted-foreground mb-1 text-[10px] uppercase tracking-wide">
              {t("similarTarget")}
            </p>
            <div className="text-sm">{renderPlanDraft(memory.content)}</div>
          </Card>

          {similarQuery.isLoading && (
            <p className="text-muted-foreground text-sm" data-testid="similar-loading">
              {t("similarLoading")}
            </p>
          )}

          {similarQuery.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="similar-error"
            >
              {errorText(similarQuery.error)}
            </p>
          )}

          {similarQuery.data && similarQuery.data.length === 0 && (
            <p className="text-muted-foreground text-sm" data-testid="similar-empty">
              {t("similarEmpty")}
            </p>
          )}

          {similarQuery.data && similarQuery.data.length > 0 && (
            <ul className="space-y-2" data-testid="similar-list">
              {similarQuery.data.map((item) => (
                <li
                  key={item.memory.id}
                  className="border-muted rounded border p-3"
                  data-testid={`similar-item-${item.memory.id}`}
                >
                  <div className="mb-1 flex items-center justify-between">
                    <Badge variant="info" data-testid={`similar-pct-${item.memory.id}`}>
                      {t("similarPercent", { pct: (item.similarity * 100).toFixed(1) })}
                    </Badge>
                    <div className="flex gap-1">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={mergeMutation.isPending}
                        onClick={() => mergeMutation.mutate(item.memory.id)}
                        data-testid={`similar-merge-${item.memory.id}`}
                      >
                        <GitMerge className="mr-1 h-3.5 w-3.5" />
                        {t("merge")}
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={discardMutation.isPending}
                        onClick={() => discardMutation.mutate(item.memory.id)}
                        data-testid={`similar-discard-${item.memory.id}`}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        {t("discard")}
                      </Button>
                    </div>
                  </div>
                  <div className="text-sm">{renderPlanDraft(item.memory.content)}</div>
                </li>
              ))}
            </ul>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
