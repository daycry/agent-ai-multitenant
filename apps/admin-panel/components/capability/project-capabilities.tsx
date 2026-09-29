"use client";

/**
 * La pestaña Capacidades del proyecto: MCP · Tools · Skills (`task_ui_20`).
 *
 * Una sola consulta (`GET /projects/{id}/capabilities`) alimenta las tres pestañas, y
 * cada una lleva debajo la sección «Capacidades disponibles» del marketplace
 * (ADR 0142), filtrada por su `kind`: es la misma entidad que escribe la ficha de la
 * instalación, ahora en el sitio donde el operador mira lo que el proyecto ya tiene.
 * Tras activar algo, la sección invalida `project-capabilities` y el despliegue aparece.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import type { McpServerConfig } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";
import { AvailableCapabilitiesSection } from "@/components/marketplace/available-capabilities-section";
import { Card, CardContent } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import {
  CAPABILITY_TABS,
  type CapabilityTab,
  LISTING_KINDS,
  type ProjectCapabilities as ProjectCapabilitiesData,
} from "@/lib/capability/project-capabilities";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";
import { cn } from "@/lib/utils";

import { CapabilityMcpCard } from "./capability-mcp-card";
import { DeployedCapabilityList } from "./deployed-capability-list";

/** Misma clave que invalida la sección de «Capacidades disponibles» al activar. */
export const PROJECT_CAPABILITIES_KEY = "project-capabilities";

export function ProjectCapabilities({
  projectId,
  initialTab = "mcp",
}: {
  projectId: string;
  initialTab?: CapabilityTab;
}) {
  const t = useT("projectCapabilities");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const [tab, setTab] = useState<CapabilityTab>(initialTab);

  const capabilitiesQuery = useQuery({
    queryKey: [PROJECT_CAPABILITIES_KEY, projectId],
    queryFn: () => apiFetch<ProjectCapabilitiesData>(`/projects/${projectId}/capabilities`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });
  // La configuración guardada de cada servidor: es lo que Probar envía tal cual.
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<{ mcp_servers?: McpServerConfig[] }>(`/projects/${projectId}`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });
  const configByName = new Map((projectQuery.data?.mcp_servers ?? []).map((s) => [s.name, s]));
  const data = capabilitiesQuery.data;

  const counts: Record<CapabilityTab, number> = {
    mcp: data?.mcp?.length ?? 0,
    tools: data?.tools?.length ?? 0,
    skills: data?.skills?.length ?? 0,
  };

  return (
    <div data-testid="project-capabilities">
      <div role="tablist" aria-label={t("title")} className="flex gap-1 border-b">
        {CAPABILITY_TABS.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            data-testid={`capabilities-tab-${id}`}
            onClick={() => setTab(id)}
            className={cn(
              "-mb-px border-b-2 px-4 py-2 text-sm font-medium",
              tab === id
                ? "border-primary text-foreground"
                : "text-muted-foreground hover:text-foreground border-transparent",
            )}
          >
            {t(id === "mcp" ? "tabMcp" : id === "tools" ? "tabTools" : "tabSkills")}
            {data ? (
              <span className="text-muted-foreground ml-1.5 text-xs">{counts[id]}</span>
            ) : null}
          </button>
        ))}
      </div>

      <div className="mt-6" role="tabpanel" data-testid={`capabilities-panel-${tab}`}>
        {capabilitiesQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">{tCommon("loading")}</p>
        ) : capabilitiesQuery.isError ? (
          <p className="text-destructive text-sm" data-testid="capabilities-error">
            {errorText(capabilitiesQuery.error)}
          </p>
        ) : tab === "mcp" ? (
          counts.mcp === 0 ? (
            <EmptyState testId="capabilities-empty-mcp" text={t("emptyMcp")} />
          ) : (
            <div className="space-y-3">
              {(data?.mcp ?? []).map((server) => (
                <CapabilityMcpCard
                  key={server.name}
                  projectId={projectId}
                  capability={server}
                  config={configByName.get(server.name)}
                />
              ))}
            </div>
          )
        ) : (
          <DeployedCapabilityList
            items={tab === "tools" ? (data?.tools ?? []) : (data?.skills ?? [])}
            emptyText={t(tab === "tools" ? "emptyTools" : "emptySkills")}
            emptyTestId={`capabilities-empty-${tab}`}
          />
        )}
      </div>

      {/* Se remonta por pestaña (`key`): cada una gobierna sus `kind` de listing. */}
      <AvailableCapabilitiesSection key={tab} projectId={projectId} kinds={LISTING_KINDS[tab]} />
    </div>
  );
}

function EmptyState({ testId, text }: { testId: string; text: string }) {
  return (
    <Card>
      <CardContent className="py-10 text-center">
        <p className="text-muted-foreground text-sm italic" data-testid={testId}>
          {text}
        </p>
      </CardContent>
    </Card>
  );
}
