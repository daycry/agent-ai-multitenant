"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import {
  ActivityBlock,
  PlansBlock,
  RunsBlock,
  SpendBlock,
  WarningsBlock,
} from "@/components/projects/overview/overview-sections";
import { StateBlock } from "@/components/shared/state-block";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { OVERVIEW_MORE_LINKS, type ProjectOverviewData } from "@/lib/projects/project-overview";

/**
 * El Resumen del proyecto (`task_ui_10`): planes activos, runs, gasto, avisos y actividad
 * de agentes de `GET /projects/{id}/overview`, cada bloque enlazando a su pestaña. Los
 * avisos van primero: es lo que pide una decisión.
 */
export function ProjectOverview({ projectId }: { projectId: string }) {
  const t = useT("projectOverview");
  const tNav = useT("nav");
  const base = `/admin/projects/${projectId}`;
  const overview = useQuery({
    queryKey: ["project-overview", projectId],
    queryFn: () => apiFetch<ProjectOverviewData>(`/projects/${projectId}/overview`),
    refetchInterval: 30_000,
  });
  const data = overview.data;

  return (
    <section className="mb-8" data-testid="project-overview" aria-label={t("title")}>
      <h2 className="mb-3 text-lg font-semibold">{t("title")}</h2>
      <StateBlock
        isLoading={overview.isLoading}
        isError={overview.isError}
        error={overview.error}
        loadingLabel={t("loading")}
        loadingSkeleton
        skeletonRows={4}
        errorTitle={t("error", { detail: "" })}
        errorTestId="overview-error"
      >
        {data && (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <WarningsBlock warnings={data.warnings} />
              </div>
              <SpendBlock spend={data.spend} base={base} />
              <div className="lg:col-span-2">
                <PlansBlock plans={data.plans} base={base} />
              </div>
              <RunsBlock runs={data.runs} />
              <div className="lg:col-span-3">
                <ActivityBlock activity={data.activity} />
              </div>
            </div>
            <nav aria-label={t("moreTitle")} data-testid="overview-more">
              <span className="text-muted-foreground mr-3 text-xs">{t("moreTitle")}</span>
              {OVERVIEW_MORE_LINKS.map((link) => (
                <Link
                  key={link.suffix}
                  href={`${base}/${link.suffix}`}
                  className="mr-3 text-xs font-medium hover:underline"
                  data-testid={`overview-more-${link.suffix}`}
                >
                  {tNav(link.labelKey)}
                </Link>
              ))}
            </nav>
          </div>
        )}
      </StateBlock>
    </section>
  );
}
