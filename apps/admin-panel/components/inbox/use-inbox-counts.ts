"use client";

/**
 * Contadores de las pestañas de la bandeja, desde las mismas cachés que su
 * contenido. Un contador NO es 0 mientras su consulta carga o si ha fallado:
 * «0» afirma que no hay nada, y eso sólo se sabe con la respuesta en la mano.
 */

import { QUESTION_CATEGORY } from "@/components/inbox/approval-card";
import { otherDecisions } from "@/components/inbox/human-queue-list";
import {
  useApprovals,
  useAssignments,
  useHumanQueue,
  useReviews,
  useUnreadNotifications,
} from "@/components/inbox/use-inbox-data";

export const INBOX_TABS = [
  "approvals",
  "questions",
  "reviews",
  "notifications",
  "assigned",
] as const;
export type InboxTabId = (typeof INBOX_TABS)[number];

export type TabCount = { state: "ok"; value: number } | { state: "loading" } | { state: "error" };

interface Source {
  isLoading: boolean;
  isError: boolean;
}

function settle(sources: Source[], value: () => number): TabCount {
  if (sources.some((s) => s.isError)) return { state: "error" };
  if (sources.some((s) => s.isLoading)) return { state: "loading" };
  return { state: "ok", value: value() };
}

export function useInboxCounts(): Record<InboxTabId, TabCount> {
  const approvals = useApprovals();
  const queue = useHumanQueue();
  const reviews = useReviews();
  const unread = useUnreadNotifications();
  const assignments = useAssignments();
  const questions = () =>
    (approvals.data ?? []).filter((r) => r.category === QUESTION_CATEGORY).length;
  return {
    approvals: settle(
      [approvals, queue],
      () => (approvals.data ?? []).length - questions() + otherDecisions(queue.data).length,
    ),
    questions: settle([approvals], questions),
    reviews: settle([reviews], () => (reviews.data ?? []).length),
    notifications: settle([unread], () => unread.data?.unread ?? 0),
    assigned: settle([assignments], () => (assignments.data ?? []).length),
  };
}

/** Todas las consultas respondieron bien y suman 0: sólo entonces «nada pendiente». */
export function isAllClear(counts: Record<InboxTabId, TabCount>): boolean {
  return INBOX_TABS.every((tab) => {
    const c = counts[tab];
    return c.state === "ok" && c.value === 0;
  });
}

/** Sin `?tab=`: Aprobaciones, salvo que no haya nada que aprobar y sí tareas asignadas. */
export function defaultTab(counts: Record<InboxTabId, TabCount>): InboxTabId {
  const a = counts.approvals;
  const s = counts.assigned;
  if (a.state === "ok" && a.value === 0 && s.state === "ok" && s.value > 0) return "assigned";
  return "approvals";
}
