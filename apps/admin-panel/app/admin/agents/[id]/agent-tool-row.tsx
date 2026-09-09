"use client";

/**
 * Una fila de la lista de tools asignables de la ficha del agente.
 *
 * Partida de `agent-tools-section.tsx` en `task_mk_13` al añadir la insignia de
 * procedencia (UI-04): la sección ya estaba en el límite de tamaño y una fila
 * con cuatro insignias y sus tooltips es una pieza con sentido propio.
 */

import { Info, Shield, Store } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import type { CapabilityProvenance } from "@/lib/agents/capability-provenance";
import { pickLang, useT } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";
import { resolveImpl, resolveSecurity } from "@/lib/tools/taxonomy";
import { cn } from "@/lib/utils";

import type { CatalogTool } from "./agent-tools-section";

export function ToolRow({
  tool,
  checked,
  canEdit,
  lang,
  onToggle,
  provenance,
}: {
  tool: CatalogTool;
  checked: boolean;
  canEdit: boolean;
  lang: Lang;
  onToggle: (toolId: string) => void;
  provenance: CapabilityProvenance | null;
}) {
  const t = useT("agents");
  const inputId = `agent-tool-${tool.id}`;
  // SINGLE source: the same shared resolvers the diagnostic uses, so a tool
  // shows identical label/variant in both screens (never the raw enum).
  const sec = resolveSecurity(tool.security_level, lang);
  const impl = resolveImpl(tool.implementation_type, lang);
  const secVariant = sec.variant;
  const implVariant = impl.variant;
  // Mismo caso que `categoryLabel`: label bilingue que viene en datos.
  const secLabel = pickLang(lang, { es: sec.labelEs, en: sec.labelEn });
  const secHelp = sec.help;
  const implLabel = pickLang(lang, { es: impl.labelEs, en: impl.labelEn });
  const implHelp = impl.help;

  return (
    <li
      // Strong, glance-readable selected state: the whole row tints and
      // gets a primary border. Hover affordance only when editable, and
      // the highlighted area === the toggle area (the <label> is full-bleed).
      className={cn(
        "rounded border transition-colors",
        checked ? "border-primary/60 bg-primary/5" : "border-border",
        canEdit && "hover:bg-muted/40",
        checked && canEdit && "hover:bg-primary/10",
      )}
      data-testid={`agent-tool-row-${tool.id}`}
      data-selected={checked ? "true" : "false"}
    >
      <div className="flex items-start gap-3 p-3">
        {/* Toggle area: the label fills the row, so clicking the name /
            description / blank space all flip the same checkbox. Read-only
            rows use the default cursor and the checkbox is disabled, so
            nothing pretends to be clickable. */}
        <label
          htmlFor={inputId}
          className={cn(
            "flex min-w-0 flex-1 items-start gap-3",
            canEdit ? "cursor-pointer" : "cursor-default",
          )}
        >
          <Checkbox
            id={inputId}
            className="mt-0.5"
            checked={checked}
            disabled={!canEdit}
            onChange={() => onToggle(tool.id)}
            data-testid={`agent-tool-checkbox-${tool.id}`}
          />
          <span className="min-w-0 flex-1">
            <span className="text-sm font-medium">{tool.name}</span>
            {tool.description && (
              <span className="text-muted-foreground mt-0.5 line-clamp-2 block text-xs">
                {tool.description}
              </span>
            )}
          </span>
        </label>

        {/* Informative badges live OUTSIDE the toggle <label> so a click on
            a badge (or its tooltip trigger) never flips the checkbox. They
            are flat (no border / no button affordance) but carry an icon
            and an accessible tooltip that opens on hover AND keyboard focus. */}
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <Tooltip content={secHelp}>
            <TooltipTrigger
              aria-label={t("toolSecurityAria", { label: secLabel, help: secHelp })}
              data-testid={`agent-tool-security-badge-${tool.id}`}
            >
              <Badge variant={secVariant} className="gap-1">
                <Shield aria-hidden="true" className="h-3 w-3" />
                {secLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          <Tooltip content={implHelp}>
            <TooltipTrigger
              aria-label={t("toolImplAria", { label: implLabel, help: implHelp })}
              data-testid={`agent-tool-impl-badge-${tool.id}`}
            >
              <Badge variant={implVariant} className="gap-1">
                <Info aria-hidden="true" className="h-3 w-3" />
                {implLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          {provenance && (
            <Tooltip content={t("capabilityFromMarketplaceTooltip", provenance)}>
              <TooltipTrigger
                aria-label={t("capabilityFromMarketplaceAria", provenance)}
                data-testid={`agent-tool-provenance-badge-${tool.id}`}
              >
                <Badge variant="info" className="gap-1">
                  <Store aria-hidden="true" className="h-3 w-3" />
                  {t("capabilityFromMarketplaceBadge")} · {provenance.listing} v{provenance.version}
                </Badge>
              </TooltipTrigger>
            </Tooltip>
          )}
          {tool.is_runtime_wired === false && (
            <Tooltip content={t("toolNotWiredTooltip")}>
              <TooltipTrigger
                aria-label={t("toolNotWiredAria")}
                data-testid={`agent-tool-not-wired-badge-${tool.id}`}
              >
                <Badge variant="warning" className="gap-1">
                  <Info aria-hidden="true" className="h-3 w-3" />
                  {t("toolNotWiredBadge")}
                </Badge>
              </TooltipTrigger>
            </Tooltip>
          )}
        </div>
      </div>
    </li>
  );
}
