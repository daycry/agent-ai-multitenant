"use client";

/**
 * Las filas del catálogo de tools y el grupo titulado que las agrupa
 * (built-in vs personalizadas del tenant).
 *
 * Pieza colocada, sacada de `page.tsx` al trocearlo (prod-16 `task_prod16_08`).
 * La fila REUTILIZA el patrón visual de `agent-tools-section` y los MISMOS
 * resolvers de taxonomía (ADR 0049), de modo que una tool dada se ve idéntica en
 * el catálogo, en la asignación y en el diagnóstico.
 */

import { Info, Pencil, Shield, Trash2, Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import type { Translator } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";
import {
  label as taxonomyLabel,
  resolveCategory,
  resolveImpl,
  resolveSecurity,
} from "@/lib/tools/taxonomy";
import { cn } from "@/lib/utils";

import type { CatalogTool } from "./tool-types";

// ---------------------------------------------------------------------------
// A titled group of tool rows (built-in vs custom)
// ---------------------------------------------------------------------------
export function ToolGroup({
  title,
  hint,
  tools,
  lang,
  t,
  canEdit,
  onEdit,
  onDelete,
  testidPrefix,
}: {
  title: string;
  hint: string;
  tools: CatalogTool[];
  lang: Lang;
  t: Translator<"tools">;
  canEdit: boolean;
  onEdit: (tool: CatalogTool) => void;
  onDelete: (tool: CatalogTool) => void;
  testidPrefix: string;
}) {
  return (
    <section data-testid={`tools-group-${testidPrefix}`}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide">{title}</h2>
        <span className="text-muted-foreground text-xs">
          {hint} · {tools.length}
        </span>
      </div>
      {tools.length === 0 ? (
        <p
          className="text-muted-foreground py-3 text-sm italic"
          data-testid={`tools-group-${testidPrefix}-empty`}
        >
          {t("groupEmpty")}
        </p>
      ) : (
        <ul className="space-y-2" data-testid={`tools-group-${testidPrefix}-list`}>
          {tools.map((tool) => (
            <ToolCatalogRow
              key={tool.id}
              tool={tool}
              lang={lang}
              t={t}
              canEdit={canEdit}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tool row — same card-row pattern as agent-tools-section's <ToolRow>:
// name + description left, the THREE facet badges right (shared resolvers).
// ---------------------------------------------------------------------------
function ToolCatalogRow({
  tool,
  lang,
  t,
  canEdit,
  onEdit,
  onDelete,
}: {
  tool: CatalogTool;
  lang: Lang;
  t: Translator<"tools">;
  canEdit: boolean;
  onEdit: (tool: CatalogTool) => void;
  onDelete: (tool: CatalogTool) => void;
}) {
  const cat = resolveCategory(tool.category, lang);
  const sec = resolveSecurity(tool.security_level, lang);
  const imp = resolveImpl(tool.implementation_type, lang);
  const catLabel = taxonomyLabel(cat, lang);
  const secLabel = taxonomyLabel(sec, lang);
  const implLabel = taxonomyLabel(imp, lang);

  return (
    <li
      className={cn("rounded border transition-colors", "border-border hover:bg-muted/40")}
      data-testid={`tool-row-${tool.id}`}
      data-builtin={tool.is_builtin ? "true" : "false"}
    >
      <div className="flex items-start gap-3 p-3">
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{tool.name}</span>
            {!tool.is_runtime_wired && (
              <Tooltip content={t("unwiredTooltip")}>
                <TooltipTrigger
                  aria-label={t("unwiredAriaLabel")}
                  data-testid={`tool-unwired-badge-${tool.id}`}
                >
                  <Badge variant="warning">{t("unwiredBadge")}</Badge>
                </TooltipTrigger>
              </Tooltip>
            )}
          </span>
          {tool.description && (
            <span className="text-muted-foreground mt-0.5 line-clamp-2 block text-xs">
              {tool.description}
            </span>
          )}
        </span>

        {/* The THREE facet badges (Función / Seguridad / Origen), flat +
            tooltip on hover AND keyboard focus — same resolvers as the
            assignment screen, so a tool reads identically everywhere. */}
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <Tooltip content={cat.help}>
            <TooltipTrigger
              aria-label={t("badgeCategoryAria", { label: catLabel, help: cat.help })}
              data-testid={`tool-category-badge-${tool.id}`}
            >
              <Badge variant={cat.variant} className="gap-1">
                <Wrench aria-hidden="true" className="h-3 w-3" />
                {catLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          <Tooltip content={sec.help}>
            <TooltipTrigger
              aria-label={t("badgeSecurityAria", { label: secLabel, help: sec.help })}
              data-testid={`tool-security-badge-${tool.id}`}
            >
              <Badge variant={sec.variant} className="gap-1">
                <Shield aria-hidden="true" className="h-3 w-3" />
                {secLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          <Tooltip content={imp.help}>
            <TooltipTrigger
              aria-label={t("badgeImplAria", { label: implLabel, help: imp.help })}
              data-testid={`tool-impl-badge-${tool.id}`}
            >
              <Badge variant={imp.variant} className="gap-1">
                <Info aria-hidden="true" className="h-3 w-3" />
                {implLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>

          {tool.is_builtin ? (
            <Badge variant="muted" data-testid={`tool-readonly-badge-${tool.id}`}>
              {t("readOnlyBadge")}
            </Badge>
          ) : (
            canEdit && (
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 px-0"
                  onClick={() => onEdit(tool)}
                  aria-label={t("editAria", { name: tool.name })}
                  data-testid={`tool-edit-${tool.id}`}
                >
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 px-0"
                  onClick={() => onDelete(tool)}
                  aria-label={t("deleteAria", { name: tool.name })}
                  data-testid={`tool-delete-${tool.id}`}
                >
                  <Trash2 className="text-destructive h-4 w-4" />
                </Button>
              </div>
            )
          )}
        </div>
      </div>
    </li>
  );
}
