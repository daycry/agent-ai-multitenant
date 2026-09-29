"use client";

/**
 * Tarjeta de una base concedida al proyecto (`task_ui_21`): nombre, categoría,
 * documentos, estado de indexado y última actualización; sus documentos viven
 * dentro, desplegables. «Quitar del proyecto» retira la concesión, no la base.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, UploadCloud } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { DocumentList, UploadDialog } from "./kb-documents";
import { KbStats } from "./kb-stats";
import type { KbDocument, KbRef } from "./kb-types";

export function ProjectKbCard({
  kb,
  projectId,
  staleReindexLabel,
}: {
  kb: KbRef & { embedding_model_id?: string };
  projectId: string;
  staleReindexLabel: string;
}) {
  const t = useT("knowledgeWizard");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  // Los documentos se piden al DESPLEGAR la tarjeta; las cifras vienen del listado.
  const docsQuery = useQuery({
    queryKey: ["kb-documents", kb.id],
    queryFn: () => apiFetch<KbDocument[]>(`/knowledge-bases/${kb.id}/documents`),
    refetchOnWindowFocus: false,
    enabled: expanded,
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["project-kbs", projectId] });
    return queryClient.invalidateQueries({ queryKey: ["kb-documents", kb.id] });
  };

  const remove = useMutation({
    mutationFn: () =>
      apiFetch<void>(`/knowledge-bases/${kb.id}/projects/${projectId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project-kbs", projectId] });
    },
  });

  const docs = docsQuery.data ?? [];
  return (
    <Card data-testid={`kb-card-${kb.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            className="flex items-center gap-2 text-left"
            data-testid={`kb-toggle-${kb.id}`}
          >
            <ChevronRight
              className={`h-4 w-4 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
            />
            <CardTitle>{kb.name}</CardTitle>
          </button>
          {kb.description ? (
            <p className="text-muted-foreground mt-1 text-xs">{kb.description}</p>
          ) : null}
          <p className="text-muted-foreground mt-1 text-xs" data-testid={`kb-category-${kb.id}`}>
            {t("categoryLabel")}: {kb.category ? kb.category.name : t("noCategory")}
          </p>
          <KbStats kbId={kb.id} stats={kb.document_stats} />
          {kb.embedding_model_stale ? (
            // ADR 0155: sello ≠ modelo activo → no aporta al camino vectorial y
            // rechaza documentos nuevos. Se dice donde el usuario sube documentos.
            <p
              className="text-danger-soft-foreground mt-1 text-xs"
              data-testid={`kb-embedding-stale-${kb.id}`}
            >
              {staleReindexLabel}
            </p>
          ) : null}
          {remove.isError ? (
            <p className="text-destructive mt-1 text-xs">{errorText(remove.error)}</p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <Button onClick={() => setUploadOpen(true)} data-testid={`kb-upload-open-${kb.id}`}>
            <UploadCloud className="mr-1 h-3.5 w-3.5" />
            {t("addDocument")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
            data-testid={`kb-remove-${kb.id}`}
          >
            {t("removeFromProject")}
          </Button>
        </div>
      </CardHeader>
      {expanded ? (
        <CardContent>
          {docsQuery.isLoading ? (
            <p className="text-muted-foreground text-sm">{t("loadingDocs")}</p>
          ) : docs.length === 0 ? (
            <p
              className="text-muted-foreground text-sm italic"
              data-testid={`kb-docs-empty-${kb.id}`}
            >
              {t("noDocumentsYet")}
            </p>
          ) : (
            <DocumentList kbId={kb.id} documents={docs} onChanged={invalidate} />
          )}
        </CardContent>
      ) : null}
      <UploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        kbId={kb.id}
        onUploaded={invalidate}
      />
    </Card>
  );
}
