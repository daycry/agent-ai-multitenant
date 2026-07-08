"use client";

/**
 * RunHistorySheet — a modal listing one task's runs (runs-visor C1).
 *
 * Opened from a Kanban card: fetches `GET /runs?task_id=` (member-accessible) and
 * lists that task's executions newest-first; clicking one opens its Timeline
 * (`/admin/executions/{id}`). Running runs auto-refresh. Empty state when the
 * task has never been executed.
 */

import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError } from "@/lib/api";
import { useLang } from "@/lib/lang-context";
import { fmtRunDuration, fmtRunMoney, fmtRunTokens, fmtRunWhen, listRuns } from "@/lib/runs";

const VERDICT_VARIANT: Record<string, BadgeVariant> = {
  running: "info",
  done: "success",
  awaiting_human_approval: "warning",
  aborted: "warning",
  cancelled: "muted",
  failed: "danger",
};

// runs-visor E1: copy del panel en los dos idiomas soportados.
const COPY = {
  es: {
    title: "Runs de la tarea",
    loading: "Cargando runs…",
    loadError: "No se pudieron cargar los runs:",
    empty: "Esta tarea no tiene ejecuciones todavía.",
    close: "Cerrar",
  },
  en: {
    title: "Task runs",
    loading: "Loading runs…",
    loadError: "Runs could not be loaded:",
    empty: "This task has no executions yet.",
    close: "Close",
  },
} as const;

export function RunHistorySheet({
  taskId,
  taskTitle,
  open,
  onOpenChange,
}: {
  taskId: string | null;
  taskTitle?: string | null;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const router = useRouter();
  const { lang } = useLang();
  const t = COPY[lang];

  const runsQuery = useQuery({
    queryKey: ["task-runs", taskId],
    queryFn: () => listRuns({ task_id: taskId as string, limit: 50 }),
    enabled: open && !!taskId,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((r) => r.verdict === "running") ? 5000 : false,
  });

  const rows = runsQuery.data ?? [];

  function openRun(id: string) {
    onOpenChange(false);
    router.push(`/admin/executions/${id}`);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="xl">
      <DialogContent data-testid="run-history-sheet">
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          {taskTitle && <p className="text-muted-foreground text-sm">{taskTitle}</p>}
        </DialogHeader>
        <DialogBody>
          {runsQuery.isLoading && <p className="text-muted-foreground text-sm">{t.loading}</p>}
          {runsQuery.isError && (
            <p className="text-destructive text-sm">
              {t.loadError}{" "}
              {runsQuery.error instanceof ApiError ? runsQuery.error.body : String(runsQuery.error)}
            </p>
          )}
          {!runsQuery.isLoading && !runsQuery.isError && rows.length === 0 && (
            <p className="text-muted-foreground text-sm italic" data-testid="run-history-empty">
              {t.empty}
            </p>
          )}
          {rows.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => openRun(r.id)}
              data-testid={`run-history-row-${r.id}`}
              className="border-border hover:bg-muted/40 flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors"
            >
              <span className="flex flex-col">
                <span className="text-sm tabular-nums">{fmtRunWhen(r.created_at)}</span>
                <span className="text-muted-foreground text-xs">
                  {r.agent_name ?? "—"} · {r.model ?? "—"}
                </span>
              </span>
              <span className="flex items-center gap-3">
                <span className="text-muted-foreground text-xs tabular-nums">
                  {fmtRunDuration(r.duration_ms)} · {fmtRunTokens(r.total_tokens)} tok ·{" "}
                  {fmtRunMoney(r)}
                </span>
                <Badge variant={VERDICT_VARIANT[r.verdict] ?? "muted"}>{r.verdict}</Badge>
              </span>
            </button>
          ))}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t.close}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
