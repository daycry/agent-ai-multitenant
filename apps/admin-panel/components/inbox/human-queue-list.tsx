"use client";

/**
 * Bandeja del humano (ADR 0123): lo que espera tu decisión y no es una tarjeta
 * de aprobación — planes por validar, runs escalados y runs aparcados. Cada fila
 * enlaza a la pantalla real donde se resuelve (la bandeja no salta los gates).
 * `GET /human-queue`, más antiguo primero. Las solicitudes de acción sensible
 * (`approval_request`) se excluyen: ya salen como tarjeta en Aprobaciones.
 */

import { useRouter } from "next/navigation";

import type { QueueItem } from "@/components/inbox/use-inbox-data";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useT } from "@/lib/i18n";

const KIND_KEYS = {
  plan_validation: "kindPlanValidation",
  run_review: "kindRunReview",
  run_approval: "kindRunApproval",
} as const;

/** Las filas de la cola que NO son una solicitud (esas ya son tarjetas). */
export function otherDecisions(items: QueueItem[] | undefined): QueueItem[] {
  return (items ?? []).filter((item) => item.kind !== "approval_request");
}

export function HumanQueueList({ items }: { items: QueueItem[] }) {
  const t = useT("inboxHub");
  const router = useRouter();
  const age = (seconds: number) => {
    if (seconds >= 24 * 3600) return t("ageDays", { value: Math.floor(seconds / (24 * 3600)) });
    if (seconds >= 3600) return t("ageHours", { value: Math.floor(seconds / 3600) });
    return t("ageMinutes", { value: Math.max(1, Math.floor(seconds / 60)) });
  };
  if (items.length === 0) return null;
  return (
    <Card data-testid="hq-list">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{t("queueTitle")}</CardTitle>
        <p className="text-muted-foreground text-xs">{t("queueHint")}</p>
      </CardHeader>
      <CardContent>
        <ul className="divide-y">
          {items.map((item, i) => {
            const key = KIND_KEYS[item.kind as keyof typeof KIND_KEYS];
            return (
              <li key={`${item.kind}-${item.id}`}>
                <button
                  type="button"
                  data-testid={`hq-item-${i}`}
                  className="hover:bg-accent flex w-full items-center gap-3 px-2 py-3 text-left transition-colors"
                  onClick={() => router.push(item.url_path)}
                >
                  <span className="flex-1">
                    <span className="block text-sm font-medium">{item.title}</span>
                    <span className="text-muted-foreground block text-xs">
                      {key ? t(key) : t("kindOther")}
                      {item.project_name ? ` · ${item.project_name}` : ""}
                    </span>
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 font-mono text-xs ${
                      item.age_seconds > 24 * 3600
                        ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                        : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {age(item.age_seconds)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
