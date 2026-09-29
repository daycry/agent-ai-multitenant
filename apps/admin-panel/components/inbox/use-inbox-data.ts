"use client";

/**
 * Las fuentes de la bandeja humana (task_ui_03). Cada hook reutiliza un endpoint
 * que ya existía y una `queryKey` estable, para que las tarjetas puedan
 * invalidarla y el contador de la pestaña y su contenido lean la misma caché.
 */

import { useQuery } from "@tanstack/react-query";

import type { InboxAssignment } from "@/app/admin/inbox/assignments-panel";
import type { ApprovalRequest } from "@/components/inbox/approval-card";
import { apiFetch } from "@/lib/api";

export interface QueueItem {
  kind: string;
  id: string;
  title: string;
  project_name: string | null;
  age_seconds: number;
  url_path: string;
}

export interface ReviewAssignment {
  assignment_id: string;
  task_id: string;
  task_title: string;
  task_description: string | null;
  project_name: string | null;
  plan_title: string | null;
  submitted_output: string | null;
  retry_count: number;
}

interface NotificationCount {
  unread: number;
}

export const useApprovals = () =>
  useQuery({
    queryKey: ["approvals"],
    queryFn: () => apiFetch<ApprovalRequest[]>("/approvals"),
    refetchOnWindowFocus: false,
  });

/** La cola agregada (ADR 0123). Refresca cada 30 s. */
export const useHumanQueue = () =>
  useQuery({
    queryKey: ["human-queue"],
    queryFn: () => apiFetch<QueueItem[]>("/human-queue"),
    refetchInterval: 30000,
  });

export const useReviews = () =>
  useQuery({
    queryKey: ["inbox", "reviews"],
    queryFn: () => apiFetch<ReviewAssignment[]>("/inbox/reviews"),
    refetchOnWindowFocus: false,
  });

export const useAssignments = () =>
  useQuery({
    queryKey: ["inbox", "assignments"],
    queryFn: () => apiFetch<InboxAssignment[]>("/inbox/assignments"),
    refetchOnWindowFocus: false,
  });

/** Sólo el contador de sin leer: la lista completa vive en la pestaña. */
export const useUnreadNotifications = () =>
  useQuery({
    queryKey: ["notification-inbox", "unread-count"],
    queryFn: () => apiFetch<NotificationCount>("/notifications/logs?limit=1&offset=0"),
    refetchOnWindowFocus: false,
  });
