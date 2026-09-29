"use client";

/**
 * La barra lateral **contextual de un proyecto**, con su vuelta al portfolio.
 *
 * `task_ui_01` la creó; `task_ui_10` la deja en las ocho entradas del plan
 * (Resumen · Tablero · Runs · Equipo · Capacidades · Conocimiento · Costes ·
 * Ajustes). Lista plana: son ocho entradas de un solo nivel.
 *
 * Desde `task_ui_12` las ocho pestañas tienen pantalla; `NavItem.disabled` queda para
 * futuras pestañas aún sin ruta (enlazarlas sería un 404).
 */

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import { NavLink } from "./nav-group-block";
import { projectNavItems, type NavItem } from "./nav-model";
import { SidebarFrame } from "./sidebar-frame";

export function SidebarProject({
  projectId,
  pathname,
  onItemClick,
  showClose = false,
  onClose,
}: {
  projectId: string;
  /**
   * Esta barra recibe el `pathname` y no el `isActive` del shell porque
   * necesita las DOS formas de coincidencia: por prefijo para las pestañas y
   * EXACTA para el resumen, que es la raíz del proyecto. Con la de prefijo, el
   * resumen se pintaría activo en las once pantallas.
   */
  pathname: string | null;
  onItemClick: () => void;
  showClose?: boolean;
  onClose?: () => void;
}) {
  const t = useT("nav");
  const items = projectNavItems(projectId);
  const overviewHref = `/admin/projects/${projectId}`;
  // Coincidencia por pestaña: la raíz (resumen) sólo EXACTA — con la de prefijo se
  // pintaría activa en todas—; el resto, por su ruta y por las pantallas que agrupa.
  const isActive = (item: NavItem) => {
    const own = item.href.split("#")[0];
    if (own === overviewHref && item.id === "overview") return pathname === overviewHref;
    const routes = [
      ...(own === overviewHref ? [] : [own]),
      ...(item.activeSuffixes ?? []).map((suffix) => overviewHref + suffix),
    ];
    return routes.some((r) => pathname === r || pathname?.startsWith(r + "/") === true);
  };

  return (
    <SidebarFrame area="project" onItemClick={onItemClick} showClose={showClose} onClose={onClose}>
      <Link
        href="/admin/projects"
        onClick={onItemClick}
        data-testid="nav-back-to-projects"
        className={cn(
          "text-sidebar-muted-foreground hover:bg-sidebar-border hover:text-sidebar-foreground",
          "mb-3 flex items-center gap-2 rounded-md px-3 py-2 text-xs font-semibold uppercase tracking-wider",
          "transition-colors",
        )}
      >
        <ArrowLeft className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{t("backToProjects")}</span>
      </Link>

      <ul className="flex flex-col gap-1" data-testid="sidebar-project">
        {items.map((item) => (
          <li key={item.href}>
            <NavLink
              href={item.href}
              labelKey={item.labelKey}
              Icon={item.Icon}
              active={isActive(item)}
              disabled={item.disabled}
              onClick={onItemClick}
              testId={`nav-project-${item.id}`}
            />
          </li>
        ))}
      </ul>
    </SidebarFrame>
  );
}
