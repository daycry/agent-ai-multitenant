"use client";

/**
 * La barra lateral **contextual de un proyecto**, con su vuelta al portfolio.
 *
 * `task_ui_01`. Es el cambio de fondo del plan: el trabajo ocurre en el
 * proyecto —planes, tareas, equipo, MCP, conocimiento, memorias, runs, costes— y
 * hasta ahora nada colgaba de él; las once pantallas de un proyecto se
 * alcanzaban desde su ficha, no desde el menú.
 *
 * Lista plana y no grupos colapsables: son once entradas de un solo nivel. Los
 * grupos existen en Trabajo y Sistema porque allí hay treinta y cuatro.
 *
 * Las pestañas que el plan añade —Tablero (`task_ui_11`), Equipo y Costes
 * (`task_ui_12`)— **no se anuncian aquí** hasta que su `page.tsx` exista:
 * `projectNavItems` sólo devuelve rutas que el panel sirve hoy, y su test lo
 * fija. Un enlace de menú a una ruta inexistente es un 404 con aspecto de
 * producto.
 */

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import { NavLink } from "./nav-group-block";
import { projectNavItems } from "./nav-model";
import { SidebarFrame } from "./sidebar-frame";

/**
 * El `data-testid` de una entrada del proyecto.
 *
 * No se deriva del último segmento como en las otras barras: el del resumen es
 * el UUID del proyecto, así que el testid cambiaría con cada proyecto y ningún
 * test podría apuntarlo. `/admin/projects/{id}` → `nav-project-overview`,
 * `/admin/projects/{id}/plans` → `nav-project-plans`.
 */
export function projectNavTestId(href: string, projectId: string): string {
  const suffix = href.replace(`/admin/projects/${projectId}`, "").replace(/^\//, "");
  return `nav-project-${suffix === "" ? "overview" : suffix}`;
}

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
  const isActive = (href: string) =>
    href === overviewHref
      ? pathname === href
      : pathname === href || pathname?.startsWith(href + "/") === true;

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
              active={isActive(item.href)}
              onClick={onItemClick}
              testId={projectNavTestId(item.href, projectId)}
            />
          </li>
        ))}
      </ul>
    </SidebarFrame>
  );
}
