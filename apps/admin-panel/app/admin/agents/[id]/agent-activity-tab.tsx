"use client";

/**
 * Pestaña Actividad de la ficha del agente (`task_ui_32`): los últimos runs de ESTE agente
 * (`GET /runs?agent_id=`, el mismo listado que reutiliza la pestaña Runs del proyecto),
 * cada uno enlazado a su detalle paso a paso.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import {
  fmtRunDuration,
  fmtRunMoney,
  fmtRunWhen,
  listRuns,
  runStatusLabel,
  runStatusVariant,
} from "@/lib/runs";

const LIMIT = 15;

export function AgentActivityTab({ agentId }: { agentId: string }) {
  const t = useT("agentTabs");
  const { lang } = useLang();
  const runs = useQuery({
    queryKey: ["agent-runs", agentId],
    queryFn: () => listRuns({ agent_id: agentId, limit: LIMIT }),
    refetchOnWindowFocus: false,
  });
  const rows = runs.data ?? [];

  return (
    <Card className="space-y-3 p-6" data-testid="agent-activity-tab">
      <p className="text-muted-foreground text-sm">{t("activityIntro")}</p>
      {runs.isError ? (
        <p className="text-danger-soft-foreground text-sm" data-testid="agent-activity-error">
          {t("activityLoadFailed")}
        </p>
      ) : rows.length === 0 && !runs.isLoading ? (
        <p className="text-muted-foreground text-sm italic" data-testid="agent-activity-empty">
          {t("activityEmpty")}
        </p>
      ) : (
        <ul className="divide-border divide-y" data-testid="agent-activity-list">
          {rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
              <span className="text-muted-foreground whitespace-nowrap text-xs tabular-nums">
                {fmtRunWhen(r.created_at)}
              </span>
              <Link
                href={`/admin/executions/${r.id}`}
                className="min-w-0 flex-1 truncate hover:underline"
                data-testid={`agent-run-link-${r.id}`}
              >
                {r.task_title ?? t("runFallback")}
              </Link>
              <span className="text-muted-foreground text-xs">{r.plan_title ?? "—"}</span>
              <Badge variant={runStatusVariant(r.verdict)}>{runStatusLabel(r.verdict, lang)}</Badge>
              <span className="text-muted-foreground text-xs tabular-nums">
                {fmtRunDuration(r.duration_ms)} · {fmtRunMoney(r)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
