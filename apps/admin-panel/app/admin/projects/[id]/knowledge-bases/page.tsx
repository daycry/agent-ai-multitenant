"use client";

/**
 * Conocimiento del proyecto (`task_ui_21`, plan ui-reestructuracion).
 *
 * Las bases de conocimiento concedidas al proyecto como tarjetas (nombre,
 * categoría, documentos, estado de indexado, última actualización) y el
 * asistente «Añadir conocimiento» de tres pasos, que concede la base al proyecto
 * sin preguntarlo, y «Activar una base existente» (catálogo global incluido) para
 * concederla sin subir nada. Crear bases sin proyecto y conceder una existente a otros
 * proyectos vive en `/admin/knowledge-bases` (misma pantalla, a nivel tenant).
 */

import { useState } from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Library, Plus } from "lucide-react";

import { ActivateExistingKb } from "@/components/knowledge/activate-existing-kb";
import { AddKnowledgeWizard } from "@/components/knowledge/add-knowledge-wizard";
import { ProjectKbCard } from "@/components/knowledge/project-kb-card";
import type { KbRef } from "@/components/knowledge/kb-types";
import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

interface ProjectKb extends KbRef {
  platform_embedding_model: string;
}

export default function ProjectKnowledgeBasesPage() {
  const errorText = useErrorText();
  const t = useT("knowledgeWizard");
  const tKbs = useT("projectKbs");
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const queryClient = useQueryClient();
  const [wizardKey, setWizardKey] = useState(0);
  const [wizardOpen, setWizardOpen] = useState(false);

  const kbsQuery = useQuery({
    queryKey: ["project-kbs", projectId],
    queryFn: () => apiFetch<ProjectKb[]>(`/projects/${projectId}/knowledge-bases`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });
  const kbs = kbsQuery.data ?? [];

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="project-kbs-page"
    >
      <ProjectBreadcrumb projectId={projectId} current={tKbs("breadcrumbCurrent")} />
      <PageHeader
        icon={<Library className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("pageTitle")}
        description={t("pageDescription")}
        data-testid="project-kbs-header"
        actions={
          <Button
            onClick={() => {
              // Un asistente nuevo por apertura: no arrastra el paso 3 anterior.
              setWizardKey((k) => k + 1);
              setWizardOpen(true);
            }}
            data-testid="add-knowledge-button"
          >
            <Plus className="mr-1 h-4 w-4" />
            {t("addKnowledge")}
          </Button>
        }
      />

      {kbsQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm">{tKbs("loading")}</p>
      ) : kbsQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="project-kbs-error">
          {errorText(kbsQuery.error)}
        </p>
      ) : kbs.length === 0 ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="project-kbs-empty">
              {t("pageEmpty")}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="mt-6 space-y-4">
          {kbs.map((kb) => (
            <ProjectKbCard
              key={kb.id}
              kb={kb}
              projectId={projectId}
              staleReindexLabel={tKbs("staleReindex", { model: kb.platform_embedding_model })}
            />
          ))}
        </div>
      )}

      <ActivateExistingKb
        projectId={projectId}
        grantedKbIds={new Set(kbs.map((k) => k.id))}
        grantedReady={kbsQuery.isSuccess}
      />

      <AddKnowledgeWizard
        key={wizardKey}
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        projectId={projectId}
        grantedKbIds={new Set(kbs.map((k) => k.id))}
        onDone={() => {
          void kbsQuery.refetch();
          void queryClient.invalidateQueries({ queryKey: ["kb-catalog"] });
        }}
      />
    </div>
  );
}
