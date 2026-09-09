"use client";

/**
 * El selector de área de la cabecera: «Trabajo» y, sólo para el System Admin,
 * «Sistema».
 *
 * `task_ui_01` + ADR 0117 (c). Es la pieza que hace visible la separación: sin
 * un selector, «el área Sistema» sería una barra lateral que aparece sola según
 * la ruta y el usuario no sabría que existe otra.
 *
 * No se pinta para quien sólo tiene un área. Un selector de una sola opción no
 * informa de nada y ocupa el sitio de lo que sí importa.
 *
 * El área de PROYECTO no está aquí a propósito (ver `Area` en `nav-model.ts`):
 * no se elige, se entra en ella desde el portfolio.
 */

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import type { Area, NavScope } from "./nav-model";
import { visibleAreas } from "./nav-model";

const AREA_LABEL: Record<"work" | "system", "areaWork" | "areaSystem"> = {
  work: "areaWork",
  system: "areaSystem",
};

export function AreaSwitcher({
  area,
  scope,
  onSelect,
}: {
  /** El área ACTIVA. Dentro de un proyecto es `project`: ninguna pestaña se marca. */
  area: Area;
  scope: NavScope;
  onSelect: (area: Area) => void;
}) {
  const t = useT("nav");
  const areas = visibleAreas(scope);

  if (areas.length < 2) return null;

  return (
    <div
      className="bg-sidebar-border/40 hidden items-center gap-1 rounded-md p-0.5 sm:flex"
      role="tablist"
      aria-label={t("areaSwitcher")}
      data-testid="area-switcher"
    >
      {areas.map((candidate) => {
        const active = candidate === area;
        return (
          <button
            key={candidate}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onSelect(candidate)}
            data-testid={`area-${candidate}`}
            className={cn(
              "rounded px-2.5 py-1 text-xs font-medium transition-colors",
              active
                ? "bg-sidebar text-sidebar-foreground shadow-sm"
                : "text-sidebar-muted-foreground hover:text-sidebar-foreground",
            )}
          >
            {t(AREA_LABEL[candidate as "work" | "system"])}
          </button>
        );
      })}
    </div>
  );
}
