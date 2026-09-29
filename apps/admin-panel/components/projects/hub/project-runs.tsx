"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import {
  fmtRunDuration,
  fmtRunMoney,
  fmtRunTokens,
  fmtRunWhen,
  listRuns,
  runStatusLabel,
  runStatusVariant,
} from "@/lib/runs";

const VERDICTS = [
  "running",
  "done",
  "awaiting_human_approval",
  "needs_human_review",
  "failed",
  "aborted",
  "cancelled",
] as const;
const PAGE_SIZE = 50;
const SELECT = "border-border bg-background rounded-md border px-2 py-1 text-sm";

interface Option {
  id: string;
  title?: string;
  name?: string;
}

/**
 * La pestaña Runs del proyecto (`task_ui_12`): los runs de UN proyecto, con filtros por plan,
 * agente y estado. Reutiliza `GET /runs` (el listado de ejecuciones, con `project_id`) y su
 * formato de fila; cada fila abre el detalle paso a paso.
 */
export function ProjectRuns({ projectId }: { projectId: string }) {
  const t = useT("projectTabs");
  const { lang } = useLang();
  const [planId, setPlanId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [verdict, setVerdict] = useState("");
  const [page, setPage] = useState(0);

  const plans = useQuery({
    queryKey: ["project-runs-plans", projectId],
    queryFn: () => apiFetch<Option[]>(`/projects/${projectId}/plans`),
  });
  const agents = useQuery({
    queryKey: ["project-runs-agents"],
    queryFn: () => apiFetch<Option[]>("/agents"),
  });

  const filters = useMemo(
    () => ({
      project_id: projectId,
      plan_id: planId || undefined,
      agent_id: agentId || undefined,
      verdict: verdict || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [projectId, planId, agentId, verdict, page],
  );
  const runs = useQuery({
    queryKey: ["project-runs", filters],
    queryFn: () => listRuns(filters),
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((r) => r.verdict === "running") ? 5000 : false,
  });
  const rows = runs.data ?? [];

  const onFilter = (set: (value: string) => void) => (value: string) => {
    set(value);
    setPage(0);
  };

  return (
    <section data-testid="project-runs" aria-label={t("runsTitle")}>
      <h2 className="mb-3 text-lg font-semibold">{t("runsTitle")}</h2>
      <div className="mb-4 flex flex-wrap items-center gap-3" data-testid="project-runs-filters">
        <label className="text-muted-foreground flex items-center gap-2 text-xs uppercase">
          {t("runsFilterPlan")}
          <select
            className={SELECT}
            value={planId}
            onChange={(e) => onFilter(setPlanId)(e.target.value)}
            data-testid="project-runs-plan-filter"
          >
            <option value="">{t("all")}</option>
            {(plans.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>
        </label>
        <label className="text-muted-foreground flex items-center gap-2 text-xs uppercase">
          {t("runsFilterAgent")}
          <select
            className={SELECT}
            value={agentId}
            onChange={(e) => onFilter(setAgentId)(e.target.value)}
            data-testid="project-runs-agent-filter"
          >
            <option value="">{t("all")}</option>
            {(agents.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-muted-foreground flex items-center gap-2 text-xs uppercase">
          {t("runsFilterStatus")}
          <select
            className={SELECT}
            value={verdict}
            onChange={(e) => onFilter(setVerdict)(e.target.value)}
            data-testid="project-runs-status-filter"
          >
            <option value="">{t("all")}</option>
            {VERDICTS.map((v) => (
              <option key={v} value={v}>
                {runStatusLabel(v, lang)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <StateBlock
        isLoading={runs.isLoading}
        isError={runs.isError}
        error={runs.error}
        loadingLabel={t("runsLoading")}
        errorTitle={t("runsError", { detail: "" })}
        errorTestId="project-runs-error"
      >
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm" data-testid="project-runs-table">
            <thead className="bg-muted/40 text-muted-foreground text-xs uppercase tracking-wide">
              <tr>
                <th className="px-3 py-2 text-left font-medium">{t("colWhen")}</th>
                <th className="px-3 py-2 text-left font-medium">{t("colPlan")}</th>
                <th className="px-3 py-2 text-left font-medium">{t("colTask")}</th>
                <th className="px-3 py-2 text-left font-medium">{t("colAgent")}</th>
                <th className="px-3 py-2 text-left font-medium">{t("colModel")}</th>
                <th className="px-3 py-2 text-left font-medium">{t("colStatus")}</th>
                <th className="px-3 py-2 text-right font-medium">{t("colDuration")}</th>
                <th className="px-3 py-2 text-right font-medium">{t("colTokens")}</th>
                <th className="px-3 py-2 text-right font-medium">{t("colCost")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td
                    colSpan={9}
                    className="text-muted-foreground px-3 py-6 text-center italic"
                    data-testid="project-runs-empty"
                  >
                    {t("runsEmpty")}
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.id}
                  data-testid={`run-row-${r.id}`}
                  className="border-border hover:bg-muted/40 border-t"
                >
                  <td className="text-muted-foreground whitespace-nowrap px-3 py-2 tabular-nums">
                    {fmtRunWhen(r.created_at)}
                  </td>
                  <td className="px-3 py-2">{r.plan_title ?? "—"}</td>
                  <td className="max-w-xs truncate px-3 py-2">
                    <Link
                      href={`/admin/executions/${r.id}`}
                      className="hover:underline"
                      data-testid={`run-link-${r.id}`}
                    >
                      {r.task_title ?? r.task_id}
                    </Link>
                  </td>
                  <td className="px-3 py-2">{r.agent_name ?? "—"}</td>
                  <td className="text-muted-foreground px-3 py-2">{r.model ?? "—"}</td>
                  <td className="px-3 py-2">
                    <Badge variant={runStatusVariant(r.verdict)}>
                      {runStatusLabel(r.verdict, lang)}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {fmtRunDuration(r.duration_ms)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {fmtRunTokens(r.total_tokens)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtRunMoney(r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </StateBlock>

      <div className="mt-4 flex items-center justify-between" data-testid="project-runs-pagination">
        <Button
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          {t("prev")}
        </Button>
        <span className="text-muted-foreground text-xs">{t("page", { page: page + 1 })}</span>
        <Button
          variant="outline"
          size="sm"
          disabled={rows.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          {t("next")}
        </Button>
      </div>
    </section>
  );
}
