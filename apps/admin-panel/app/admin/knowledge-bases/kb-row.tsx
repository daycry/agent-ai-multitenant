"use client";

/**
 * Fila de una Knowledge Base en el listado (prod-16 `task_prod16_08`).
 *
 * Salió de `kb-sections.tsx`, que el tramo de modularización #9 había dejado en
 * 782 líneas con las cinco piezas del módulo dentro. La fila es la única que se
 * rinde N veces por pantalla y la única con estado propio (el desplegable de
 * documentos), así que es la que más se lee y la que menos tiene que ver con los
 * diálogos.
 */

import { useState } from "react";
import { ChevronRight, Pencil, Share2, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { useT } from "@/lib/i18n";
import { renderPlanDraft } from "@/lib/plan-draft-md";

import { KbDocumentsPanel } from "./kb-documents-panel";
import { type KnowledgeBase } from "./kb-types";

export function KbRow({
  kb,
  onEdit,
  onDelete,
  onGrant,
  onShowAssignments,
}: {
  kb: KnowledgeBase;
  onEdit: () => void;
  onDelete: () => void;
  onGrant: () => void;
  onShowAssignments: () => void;
}) {
  const t = useT("knowledgeBases");
  const [expanded, setExpanded] = useState(false);

  return (
    <Card data-testid={`kb-${kb.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
          data-testid={`kb-toggle-docs-${kb.id}`}
          aria-expanded={expanded}
        >
          <ChevronRight
            className={`mt-1 h-4 w-4 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
          />
          <span className="min-w-0">
            <CardTitle className="flex items-center gap-2 text-base">
              {kb.name}
              {kb.is_builtin && (
                <Badge variant="muted" data-testid={`kb-builtin-badge-${kb.id}`}>
                  {t("builtinBadge")}
                </Badge>
              )}
            </CardTitle>
            {/* "embedding:" es el nombre del campo del backend, no texto de UI.
                El valor es el SELLO de la KB (ADR 0155); si no es el modelo
                activo de la plataforma, la fila lo dice en vez de callarlo. */}
            <span className="text-muted-foreground mt-1 block font-mono text-xs">
              embedding: {kb.embedding_model_id}
            </span>
            {kb.embedding_model_stale && (
              <Badge variant="muted" data-testid={`kb-embedding-stale-${kb.id}`}>
                {t("embeddingStale")}
              </Badge>
            )}
          </span>
        </button>
        <Button
          variant="outline"
          size="sm"
          onClick={onShowAssignments}
          data-testid={`kb-assignments-${kb.id}`}
          title={t("assignmentsTitle")}
        >
          {t("assignments")}
        </Button>
        <RoleGuard min="tenant_admin">
          <div className="flex flex-row items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={onGrant}
              data-testid={`kb-grant-${kb.id}`}
              title={t("grantTitle")}
            >
              <Share2 className="mr-1 h-3.5 w-3.5" />
              {t("grant")}
            </Button>
            {/* Plan 06.12: las KB built-in son read-only para el tenant
                (el backend rechaza PUT/DELETE). Solo Grant + Asignaciones. */}
            {!kb.is_builtin && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onEdit}
                  data-testid={`kb-edit-${kb.id}`}
                  aria-label={t("editTitle")}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={onDelete}
                  data-testid={`kb-delete-${kb.id}`}
                  aria-label={t("deleteTitle")}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </>
            )}
          </div>
        </RoleGuard>
      </CardHeader>
      {kb.description && (
        <CardContent className="pb-2">
          <div className="text-sm">{renderPlanDraft(kb.description)}</div>
        </CardContent>
      )}
      {expanded && (
        <CardContent className="pt-0">
          <KbDocumentsPanel kbId={kb.id} />
        </CardContent>
      )}
    </Card>
  );
}
