"use client";

/**
 * Runs — the Work-menu list of agent executions (runs-visor B2).
 *
 * Lists this tenant's runs newest-first (`GET /runs`, member-accessible),
 * filterable by verdict, paginated. Rows with a `running` verdict auto-refresh
 * (the denormalized tokens/cost/duration are only persisted at finalize, so a
 * running row shows live status + elapsed but no metrics until it ends — the
 * live numbers are in the run's detail via its WebSocket). A row opens the
 * execution Timeline (`/admin/executions/{id}`). Copy ES/EN vía lang-context
 * (runs-visor E1).
 */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
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

// F49: every persistible run verdict the filter can select on — including the
// human-attention states `awaiting_human_approval` and `needs_human_review`
// (ADR 0087) that were previously absent (so those runs were unfilterable).
const VERDICTS = [
  "",
  "running",
  "done",
  "awaiting_human_approval",
  "needs_human_review",
  "failed",
  "aborted",
  "cancelled",
] as const;
const PAGE_SIZE = 50;

const COPY = {
  es: {
    description:
      "Ejecuciones de los agentes, las más recientes primero. Tokens, tiempo y coste por run; abre una para ver el detalle paso a paso (en vivo si está en curso).",
    state: "Estado",
    all: "Todos",
    updating: "Actualizando…",
    loadError: "No se pudieron cargar los runs:",
    cols: {
      when: "Fecha",
      plan: "Plan",
      task: "Tarea",
      agent: "Agente",
      model: "Modelo",
      status: "Estado",
      duration: "Duración",
      tokens: "Tokens",
      cost: "Coste",
    },
    loading: "Cargando runs…",
    empty: "No hay runs todavía.",
    prev: "Anterior",
    next: "Siguiente",
    page: "Página",
  },
  en: {
    description:
      "Agent executions, newest first. Tokens, time and cost per run; open one for the step-by-step detail (live while running).",
    state: "Status",
    all: "All",
    updating: "Refreshing…",
    loadError: "Runs could not be loaded:",
    cols: {
      when: "Date",
      plan: "Plan",
      task: "Task",
      agent: "Agent",
      model: "Model",
      status: "Status",
      duration: "Duration",
      tokens: "Tokens",
      cost: "Cost",
    },
    loading: "Loading runs…",
    empty: "No runs yet.",
    prev: "Previous",
    next: "Next",
    page: "Page",
  },
} as const;

export default function RunsPage() {
  const router = useRouter();
  const { lang } = useLang();
  const t = COPY[lang];
  const [verdict, setVerdict] = useState<string>("");
  const [page, setPage] = useState(0);

  const filters = useMemo(
    () => ({ verdict: verdict || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [verdict, page],
  );

  const runsQuery = useQuery({
    queryKey: ["runs", filters],
    queryFn: () => listRuns(filters),
    refetchOnWindowFocus: false,
    // Auto-refresh while any run on the page is in progress.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((r) => r.verdict === "running") ? 5000 : false,
  });

  const rows = runsQuery.data ?? [];

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<ListChecks className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Runs"
        description={t.description}
      />

      <div className="mb-4 flex flex-wrap items-center gap-2" data-testid="runs-filters">
        <label className="text-muted-foreground text-xs uppercase tracking-wide">{t.state}</label>
        <select
          value={verdict}
          onChange={(e) => {
            setVerdict(e.target.value);
            setPage(0);
          }}
          data-testid="runs-verdict-filter"
          className="border-border bg-background rounded-md border px-2 py-1 text-sm"
        >
          {VERDICTS.map((v) => (
            <option key={v} value={v}>
              {v === "" ? t.all : runStatusLabel(v, lang)}
            </option>
          ))}
        </select>
        {runsQuery.isFetching && (
          <span className="text-muted-foreground text-xs">{t.updating}</span>
        )}
      </div>

      {runsQuery.isError && (
        <Card className="border-destructive p-4" data-testid="runs-error">
          <p className="text-destructive text-sm">
            {t.loadError}{" "}
            {runsQuery.error instanceof ApiError ? runsQuery.error.body : String(runsQuery.error)}
          </p>
        </Card>
      )}

      {!runsQuery.isError && (
        <Card className="overflow-x-auto p-0">
          <table className="w-full text-sm" data-testid="runs-table">
            <thead className="bg-muted/40 text-muted-foreground text-xs uppercase tracking-wide">
              <tr>
                <th className="px-3 py-2 text-left font-medium">{t.cols.when}</th>
                <th className="px-3 py-2 text-left font-medium">{t.cols.plan}</th>
                <th className="px-3 py-2 text-left font-medium">{t.cols.task}</th>
                <th className="px-3 py-2 text-left font-medium">{t.cols.agent}</th>
                <th className="px-3 py-2 text-left font-medium">{t.cols.model}</th>
                <th className="px-3 py-2 text-left font-medium">{t.cols.status}</th>
                <th className="px-3 py-2 text-right font-medium">{t.cols.duration}</th>
                <th className="px-3 py-2 text-right font-medium">{t.cols.tokens}</th>
                <th className="px-3 py-2 text-right font-medium">{t.cols.cost}</th>
              </tr>
            </thead>
            <tbody>
              {runsQuery.isLoading && (
                <tr>
                  <td colSpan={9} className="text-muted-foreground px-3 py-6 text-center">
                    {t.loading}
                  </td>
                </tr>
              )}
              {!runsQuery.isLoading && rows.length === 0 && (
                <tr>
                  <td
                    colSpan={9}
                    className="text-muted-foreground px-3 py-6 text-center italic"
                    data-testid="runs-empty"
                  >
                    {t.empty}
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => router.push(`/admin/executions/${r.id}`)}
                  data-testid={`run-row-${r.id}`}
                  className="border-border hover:bg-muted/40 cursor-pointer border-t"
                >
                  <td className="text-muted-foreground whitespace-nowrap px-3 py-2 tabular-nums">
                    {fmtRunWhen(r.created_at)}
                  </td>
                  <td className="px-3 py-2">{r.plan_title ?? "—"}</td>
                  <td className="max-w-xs truncate px-3 py-2">{r.task_title ?? r.task_id}</td>
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
      )}

      <div className="mt-4 flex items-center justify-between" data-testid="runs-pagination">
        <Button
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          {t.prev}
        </Button>
        <span className="text-muted-foreground text-xs">
          {t.page} {page + 1}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={rows.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
        >
          {t.next}
        </Button>
      </div>
    </div>
  );
}
