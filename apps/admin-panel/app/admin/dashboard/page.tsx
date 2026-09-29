"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import Link from "next/link";

import { PendingSection } from "@/components/dashboard/pending-section";
import {
  ActivitySection,
  IssuesSection,
  McpSection,
  type DegradedService,
} from "@/components/dashboard/status-sections";
import { ProjectsSection, SpendSection } from "@/components/dashboard/work-sections";
import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { apiFetch } from "@/lib/api";
import { isNoTenantError, type TenantDashboard } from "@/lib/dashboard/tenant-dashboard";
import { useT } from "@/lib/i18n";

interface SystemHealthResponse {
  status: string;
  services: { name: string; status: string }[];
}

/**
 * El dashboard del tenant (`task_ui_02`): seis secciones de `GET /tenant/dashboard`,
 * en el orden que fijó el operador. La salud del stack sólo se enseña cuando algo no
 * está `ok`, y sólo si el usuario puede leerla: un miembro sin acceso a
 * `/admin/system-health` no ve un error, ve el dashboard.
 */
export default function DashboardPage() {
  const t = useT("tenantDashboard");
  const dashboard = useQuery({
    queryKey: ["tenant-dashboard"],
    queryFn: () => apiFetch<TenantDashboard>("/tenant/dashboard"),
    refetchInterval: 30_000,
  });
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: () => apiFetch<SystemHealthResponse>("/admin/system-health"),
    refetchInterval: 30_000,
    retry: false,
  });
  const degraded: DegradedService[] = (health.data?.services ?? []).filter(
    (s) => s.status !== "ok",
  );
  const data = dashboard.data;

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Activity className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
      />
      {dashboard.isError && isNoTenantError(dashboard.error) && (
        <div className="border-border rounded-lg border p-4" data-testid="dashboard-no-tenant">
          <p className="font-medium">{t("noTenantTitle")}</p>
          <p className="text-muted-foreground mt-1 text-sm">{t("noTenantBody")}</p>
          <Link href="/select-tenant" className="mt-2 inline-block text-sm font-medium underline">
            {t("noTenantLink")}
          </Link>
        </div>
      )}
      <StateBlock
        isLoading={dashboard.isLoading}
        isError={dashboard.isError && !isNoTenantError(dashboard.error)}
        error={dashboard.error}
        loadingLabel={t("loading")}
        loadingSkeleton
        skeletonRows={6}
        errorTitle={t("error", { detail: "" })}
        errorTestId="dashboard-error"
      >
        {data && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3" data-testid="dashboard-grid">
            <div className="lg:col-span-2">
              <PendingSection pending={data.pending} />
            </div>
            {data.spend.reason !== "forbidden" && <SpendSection spend={data.spend} />}
            <div className="lg:col-span-3">
              <IssuesSection issues={data.issues} services={degraded} />
            </div>
            <div className="lg:col-span-2">
              <ProjectsSection projects={data.projects} />
            </div>
            <McpSection mcp={data.mcp} />
            <div className="lg:col-span-3">
              <ActivitySection activity={data.activity} />
            </div>
          </div>
        )}
      </StateBlock>
    </div>
  );
}
