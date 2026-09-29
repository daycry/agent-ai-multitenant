"use client";

/**
 * Pestañas Aprobaciones y Preguntas: las dos son `GET /approvals`, separadas por
 * `category === "human_question"` (las preguntas `ask_human`, ADR 0114).
 * Aprobaciones añade además las otras decisiones de la cola humana (ADR 0123).
 */

import { ShieldCheck } from "lucide-react";

import { ApprovalCard, QUESTION_CATEGORY } from "@/components/inbox/approval-card";
import { HumanQueueList, otherDecisions } from "@/components/inbox/human-queue-list";
import { useApprovals, useHumanQueue } from "@/components/inbox/use-inbox-data";
import { StateBlock } from "@/components/shared/state-block";
import { useT } from "@/lib/i18n";

export function RequestsTab({ kind }: { kind: "approvals" | "questions" }) {
  const t = useT("inboxHub");
  const query = useApprovals();
  const queue = useHumanQueue();
  const isQuestions = kind === "questions";
  const rows = (query.data ?? []).filter((r) => (r.category === QUESTION_CATEGORY) === isQuestions);
  const extra = isQuestions ? [] : otherDecisions(queue.data);

  return (
    <div className="space-y-4">
      <StateBlock
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        isEmpty={Boolean(query.data) && rows.length === 0}
        loadingLabel={t("loading")}
        errorTitle={t("loadError")}
        errorTestId="approvals-error"
        emptyIcon={ShieldCheck}
        emptyTitle={isQuestions ? t("questionsEmpty") : t("approvalsEmpty")}
        emptyDescription={isQuestions ? t("questionsEmptyHint") : t("approvalsEmptyHint")}
        emptyTestId="approvals-empty"
      >
        <ul className="space-y-3" data-testid="approvals-list">
          {rows.map((request) => (
            <li key={request.id}>
              <ApprovalCard request={request} />
            </li>
          ))}
        </ul>
      </StateBlock>
      {!isQuestions && (
        <StateBlock
          isError={queue.isError}
          error={queue.error}
          errorTitle={t("queueLoadError")}
          errorTestId="human-queue-error"
        />
      )}
      <HumanQueueList items={extra} />
    </div>
  );
}
