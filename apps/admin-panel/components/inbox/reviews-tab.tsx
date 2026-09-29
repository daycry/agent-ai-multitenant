"use client";

/**
 * Pestaña Revisiones: las revisiones entre pares que tienes asignadas
 * (`GET /inbox/reviews`, task_16_11). Aprobar la entrega es
 * `POST /inbox/reviews/{id}/approve`; pedir cambios es
 * `POST /inbox/reviews/{id}/reject` con `feedback_text` obligatorio.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardCheck, X } from "lucide-react";

import { useReviews, type ReviewAssignment } from "@/components/inbox/use-inbox-data";
import { StateBlock } from "@/components/shared/state-block";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

function ReviewCard({ item }: { item: ReviewAssignment }) {
  const t = useT("inboxHub");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState<string | null>(null);

  const verdict = useMutation({
    mutationFn: (approve: boolean) =>
      apiFetch(
        `/inbox/reviews/${item.assignment_id}/${approve ? "approve" : "reject"}`,
        approve ? { method: "POST" } : { method: "POST", body: { feedback_text: feedback.trim() } },
      ),
    onSuccess: async () => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ["inbox", "reviews"] });
    },
    onError: (err) => setError(errorText(err)),
  });

  return (
    <Card data-testid={`review-card-${item.assignment_id}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{item.task_title}</CardTitle>
        <p className="text-muted-foreground text-xs">
          {item.project_name ?? ""}
          {item.plan_title ? ` · ${item.plan_title}` : ""} ·{" "}
          {t("reviewRetries", { n: item.retry_count })}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <pre className="bg-muted/40 max-h-64 overflow-auto rounded-md p-2 text-xs whitespace-pre-wrap">
          {item.submitted_output ?? t("reviewNoOutput")}
        </pre>
        <MarkdownTextarea
          data-testid={`review-feedback-${item.assignment_id}`}
          value={feedback}
          onChange={setFeedback}
          placeholder={t("reviewFeedbackPlaceholder")}
          rows={2}
        />
        {error && (
          <p
            className="text-destructive text-xs"
            data-testid={`review-error-${item.assignment_id}`}
          >
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <Button
            data-testid={`review-approve-${item.assignment_id}`}
            disabled={verdict.isPending}
            onClick={() => verdict.mutate(true)}
          >
            <Check className="mr-1.5 h-4 w-4" />
            {t("reviewApprove")}
          </Button>
          <Button
            variant="outline"
            data-testid={`review-reject-${item.assignment_id}`}
            disabled={verdict.isPending || !feedback.trim()}
            onClick={() => verdict.mutate(false)}
          >
            <X className="mr-1.5 h-4 w-4" />
            {t("reviewReject")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function ReviewsTab() {
  const t = useT("inboxHub");
  const query = useReviews();
  const rows = query.data ?? [];
  return (
    <StateBlock
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      isEmpty={Boolean(query.data) && rows.length === 0}
      loadingLabel={t("loading")}
      errorTitle={t("loadError")}
      errorTestId="reviews-error"
      emptyIcon={ClipboardCheck}
      emptyTitle={t("reviewsEmpty")}
      emptyDescription={t("reviewsEmptyHint")}
      emptyTestId="reviews-empty"
    >
      <ul className="space-y-3" data-testid="reviews-list">
        {rows.map((item) => (
          <li key={item.assignment_id}>
            <ReviewCard item={item} />
          </li>
        ))}
      </ul>
    </StateBlock>
  );
}
