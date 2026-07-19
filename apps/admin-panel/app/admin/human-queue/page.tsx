"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { DoorOpen } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";

/**
 * Bandeja del humano (ADR 0123): TODO lo que espera tu decisión, en una
 * lista ordenada por antigüedad — planes por validar, acciones sensibles por
 * aprobar, runs escalados y runs aparcados en una aprobación. Cada fila
 * enlaza a la pantalla real donde se resuelve (la bandeja no salta los
 * gates). Refresco cada 30 s.
 */

interface QueueItem {
  kind: string;
  id: string;
  title: string;
  project_name: string | null;
  age_seconds: number;
  url_path: string;
}

const KIND_LABEL: Record<string, { label: string; emoji: string }> = {
  plan_validation: { label: "Plan por validar", emoji: "📋" },
  approval_request: { label: "Acción sensible", emoji: "🔐" },
  run_review: { label: "Run escalado", emoji: "🚨" },
  run_approval: { label: "Run esperando aprobación", emoji: "⏸️" },
};

export function formatAge(seconds: number): string {
  if (seconds >= 24 * 3600) return `${Math.floor(seconds / (24 * 3600))} d`;
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.max(1, Math.floor(seconds / 60))} min`;
}

export default function HumanQueuePage() {
  const router = useRouter();
  const queue = useQuery({
    queryKey: ["human-queue"],
    queryFn: () => apiFetch<QueueItem[]>("/human-queue"),
    refetchInterval: 30000,
  });

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-3">
        <DoorOpen className="h-7 w-7" aria-hidden="true" />
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Esperan tu decisión</h1>
          <p className="text-muted-foreground text-sm">
            Todo lo que está parado hasta que un humano actúe, lo más antiguo primero.
          </p>
        </div>
      </div>

      <Card>
        <CardContent className="pt-4">
          {queue.isLoading && <Spinner className="h-5 w-5" />}
          {!queue.isLoading && (queue.data ?? []).length === 0 && (
            <p className="text-muted-foreground text-sm" data-testid="hq-empty">
              Nada espera tu decisión. 🎉
            </p>
          )}
          <ul className="divide-y">
            {(queue.data ?? []).map((item, i) => {
              const kind = KIND_LABEL[item.kind] ?? { label: item.kind, emoji: "•" };
              return (
                <li key={`${item.kind}-${item.id}`}>
                  <button
                    type="button"
                    data-testid={`hq-item-${i}`}
                    className="hover:bg-accent flex w-full items-center gap-3 px-2 py-3 text-left transition-colors"
                    onClick={() => router.push(item.url_path)}
                  >
                    <span className="text-xl" aria-hidden="true">
                      {kind.emoji}
                    </span>
                    <span className="flex-1">
                      <span className="block text-sm font-medium">{item.title}</span>
                      <span className="text-muted-foreground block text-xs">
                        {kind.label}
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
                      {formatAge(item.age_seconds)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
