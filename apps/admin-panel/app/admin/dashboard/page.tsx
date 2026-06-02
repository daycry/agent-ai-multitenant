"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  CheckCircle2,
  CircleAlert,
  CircleX,
  FolderKanban,
  Server,
  Users,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Card, CardContent, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";

interface ServiceHealth {
  name: string;
  status: "ok" | "degraded" | "down" | string;
  detail?: string | null;
}

interface SystemHealthResponse {
  status: string;
  services: ServiceHealth[];
}

interface Counts {
  agents: number;
  teams: number;
  projects: number;
}

const statusClasses: Record<string, string> = {
  ok: "bg-success-soft text-success-soft-foreground",
  degraded: "bg-warning-soft text-warning-soft-foreground",
  down: "bg-danger-soft text-danger-soft-foreground",
};

function StatusIcon({ status, className = "h-4 w-4" }: { status: string; className?: string }) {
  const cls = cn(className, "shrink-0");
  if (status === "ok")
    return <CheckCircle2 className={cn(cls, "text-success")} aria-hidden="true" />;
  if (status === "degraded")
    return <CircleAlert className={cn(cls, "text-warning")} aria-hidden="true" />;
  if (status === "down") return <CircleX className={cn(cls, "text-danger")} aria-hidden="true" />;
  return <Activity className={cn(cls, "text-muted-foreground")} aria-hidden="true" />;
}

function KpiCard({
  icon,
  label,
  value,
  hint,
  testid,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  hint?: string;
  testid?: string;
}) {
  return (
    <Card className="overflow-hidden" data-testid={testid}>
      <CardContent className="flex items-center gap-4 p-5">
        <div
          className={cn(
            "bg-primary/10 text-primary flex h-12 w-12 shrink-0 items-center justify-center",
            "rounded-lg",
          )}
        >
          {icon}
        </div>
        <div className="flex flex-col">
          <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
            {label}
          </p>
          <p className="text-2xl font-semibold leading-tight tracking-tight">{value}</p>
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const health = useQuery({
    queryKey: ["system-health"],
    queryFn: () => apiFetch<SystemHealthResponse>("/admin/system-health"),
    refetchInterval: 30_000,
    retry: 1,
  });

  // KPI counts pulled in parallel. Each endpoint is small; failures
  // degrade gracefully (the card just shows "—").
  const agents = useQuery({
    queryKey: ["agents", "count"],
    queryFn: () => apiFetch<unknown[]>("/agents"),
    refetchOnWindowFocus: false,
  });
  const teams = useQuery({
    queryKey: ["teams", "count"],
    queryFn: () => apiFetch<unknown[]>("/teams"),
    refetchOnWindowFocus: false,
  });
  const projects = useQuery({
    queryKey: ["projects", "count"],
    queryFn: () => apiFetch<unknown[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  const counts: Counts = {
    agents: agents.data?.length ?? 0,
    teams: teams.data?.length ?? 0,
    projects: projects.data?.length ?? 0,
  };

  const overall = health.data?.status ?? "—";

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Activity className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Dashboard"
        description={
          health.data
            ? `Estado general: ${overall} · auto-refresh cada 30 s`
            : "Estado del stack y catálogo del tenant."
        }
      />

      {/* ============================= KPI row ============================= */}
      <section
        className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
        data-testid="kpi-grid"
      >
        <KpiCard
          icon={<Server className="h-5 w-5" />}
          label="Servicios"
          value={health.data?.services.length ?? "—"}
          hint={`Overall: ${overall}`}
          testid="kpi-services"
        />
        <KpiCard
          icon={<Bot className="h-5 w-5" />}
          label="Agentes visibles"
          value={agents.isLoading ? "…" : counts.agents}
          hint="Builtin + tenant + locales"
          testid="kpi-agents"
        />
        <KpiCard
          icon={<Users className="h-5 w-5" />}
          label="Equipos"
          value={teams.isLoading ? "…" : counts.teams}
          hint="Plantillas + propios"
          testid="kpi-teams"
        />
        <KpiCard
          icon={<FolderKanban className="h-5 w-5" />}
          label="Proyectos"
          value={projects.isLoading ? "…" : counts.projects}
          hint="Excluye templates"
          testid="kpi-projects"
        />
      </section>

      {/* ============================= Services health ============================= */}
      <section>
        <h2 className="mb-3 text-base font-semibold tracking-tight">Salud de servicios</h2>

        <StateBlock
          isLoading={health.isLoading}
          isError={health.isError}
          error={health.error}
          loadingLabel="Loading services…"
          loadingSkeleton
          skeletonRows={5}
          errorTitle="Could not load services"
          errorTestId="dashboard-error"
        >
          {health.data && (
            <div
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5"
              data-testid="services-grid"
            >
              {health.data.services.map((service) => (
                <Card
                  key={service.name}
                  data-testid={`service-${service.name}`}
                  className="flex h-full flex-col items-center justify-center gap-2 p-5 text-center"
                >
                  <StatusIcon status={service.status} className="h-7 w-7" />
                  <CardTitle className="text-base capitalize">{service.name}</CardTitle>
                  <span
                    className={cn(
                      "rounded px-2 py-0.5 text-xs font-medium",
                      statusClasses[service.status] ?? "bg-muted text-muted-foreground",
                    )}
                    data-testid={`service-${service.name}-status`}
                  >
                    {service.status}
                  </span>
                  {service.detail && (
                    <p className="text-muted-foreground text-xs">{service.detail}</p>
                  )}
                </Card>
              ))}
            </div>
          )}
        </StateBlock>
      </section>
    </div>
  );
}
