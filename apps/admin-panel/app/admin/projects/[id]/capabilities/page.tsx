"use client";

/**
 * Capacidades del proyecto en una pantalla (`task_ui_20`, plan ui-reestructuracion):
 * MCP · Tools · Skills con su procedencia, y «Capacidades disponibles» del marketplace
 * (ADR 0142) fundida dentro. Las pantallas de configuración de MCP y el diagnóstico por
 * agente siguen en sus rutas de siempre.
 */

import { Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { Boxes } from "lucide-react";

import { ProjectCapabilities } from "@/components/capability/project-capabilities";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { PageHeader } from "@/components/layout/page-header";
import { parseTab } from "@/lib/capability/project-capabilities";
import { useT } from "@/lib/i18n";

function CapabilitiesBody({ projectId }: { projectId: string }) {
  const tab = parseTab(useSearchParams().get("tab"));
  return <ProjectCapabilities projectId={projectId} initialTab={tab} />;
}

export default function ProjectCapabilitiesPage() {
  const t = useT("projectCapabilities");
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="project-capabilities-page"
    >
      <ProjectBreadcrumb projectId={projectId} current={t("breadcrumbCurrent")} />
      <PageHeader
        icon={<Boxes className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
      />
      <div className="mt-6">
        <Suspense fallback={null}>
          <CapabilitiesBody projectId={projectId} />
        </Suspense>
      </div>
    </div>
  );
}
