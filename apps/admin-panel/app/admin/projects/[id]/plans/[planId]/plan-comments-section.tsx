"use client";

// Comentarios del plan/tareas (rail PlanComment → prompt del agente).
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { renderPlanDraft } from "@/lib/plan-draft-md";

// --------------------------------------------------------------------------
// Inline comments (task_03_21)
// --------------------------------------------------------------------------
interface PlanCommentResponse {
  id: string;
  plan_id: string;
  target_kind: string;
  target_ref: string | null;
  author_user_id: string | null;
  content: string;
  created_at: string;
}

export function CommentsSection({ planId, taskIds }: { planId: string; taskIds: string[] }) {
  const t = useT("planDetail");
  const queryClient = useQueryClient();
  const [targetKind, setTargetKind] = useState<"plan" | "task">("plan");
  const [targetRef, setTargetRef] = useState<string>("");
  const [content, setContent] = useState("");

  const commentsQuery = useQuery({
    queryKey: ["plan-comments", planId],
    queryFn: () => apiFetch<PlanCommentResponse[]>(`/plans/${planId}/comments`),
    refetchOnWindowFocus: false,
  });

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<PlanCommentResponse>(`/plans/${planId}/comments`, {
        method: "POST",
        body:
          targetKind === "task" && targetRef
            ? { target_kind: "task", target_ref: targetRef, content }
            : { target_kind: "plan", content },
      }),
    onSuccess: (created) => {
      queryClient.setQueryData<PlanCommentResponse[]>(["plan-comments", planId], (prev) =>
        prev ? [...prev, created] : [created],
      );
      setContent("");
    },
  });

  const canSubmit =
    content.trim().length > 0 &&
    !mutation.isPending &&
    (targetKind !== "task" || taskIds.includes(targetRef));

  return (
    <Card className="mt-6" data-testid="plan-comments">
      <CardHeader>
        <CardTitle>{t("commentsTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="mb-4 space-y-2" data-testid="plan-comments-list">
          {(commentsQuery.data ?? []).map((c) => (
            <li
              key={c.id}
              data-testid={`plan-comment-${c.id}`}
              data-target-kind={c.target_kind}
              data-target-ref={c.target_ref ?? ""}
              className="border-muted rounded border px-3 py-2 text-sm"
            >
              <p className="text-muted-foreground mb-1 text-[10px] uppercase tracking-wide">
                {c.target_kind === "task" ? (
                  <>
                    {t("commentOnTask")} <span className="font-mono">{c.target_ref}</span>
                  </>
                ) : c.target_kind === "phase" ? (
                  <>{t("commentOnPhase", { ref: c.target_ref ?? "" })}</>
                ) : (
                  <>{t("commentOnPlan")}</>
                )}
              </p>
              <div>{renderPlanDraft(c.content)}</div>
            </li>
          ))}
          {(commentsQuery.data ?? []).length === 0 ? (
            <p className="text-muted-foreground text-xs italic" data-testid="plan-comments-empty">
              {t("commentsEmpty")}
            </p>
          ) : null}
        </ul>

        <form
          className="space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!canSubmit) return;
            mutation.mutate();
          }}
          data-testid="plan-comment-form"
        >
          <div className="flex gap-2">
            <select
              value={targetKind}
              onChange={(e) => {
                const next = e.target.value as "plan" | "task";
                setTargetKind(next);
                if (next === "task" && taskIds.length > 0) setTargetRef(taskIds[0]);
                else setTargetRef("");
              }}
              data-testid="plan-comment-target-kind"
              className="bg-background border-muted rounded border px-2 py-1 text-sm"
            >
              <option value="plan">{t("commentOnPlan")}</option>
              <option value="task" disabled={taskIds.length === 0}>
                {t("commentOnATask")}
              </option>
            </select>
            {targetKind === "task" ? (
              <select
                value={targetRef}
                onChange={(e) => setTargetRef(e.target.value)}
                data-testid="plan-comment-target-ref"
                className="bg-background border-muted rounded border px-2 py-1 text-sm font-mono"
              >
                {taskIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
          <MarkdownTextarea
            value={content}
            onChange={setContent}
            placeholder={t("commentPlaceholder")}
            rows={4}
            data-testid="plan-comment-content"
          />
          <div className="flex justify-end">
            <Button type="submit" disabled={!canSubmit} data-testid="plan-comment-submit">
              {t("commentSubmit")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
