"use client";

/**
 * Un grupo colapsable del menú, con su preferencia persistida.
 *
 * Sale de `admin-shell.tsx` en `task_ui_01` sin cambiar una línea de su
 * comportamiento: lo usan las barras de Trabajo y de Sistema, y duplicarlo en
 * las dos habría sido la forma segura de que se separasen.
 *
 * Se conservan el `data-testid` del grupo (`nav-group-${id}`), el de cada ítem
 * (`nav-${último-segmento}`) y la clave de localStorage: los e2e dependen de
 * los dos primeros y la tercera es la preferencia que ya tiene la gente
 * guardada en su navegador.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { ChevronDown } from "lucide-react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import type { NavGroup } from "./nav-model";

export const LS_KEY_PREFIX = "agentic.nav.group.";

export function NavGroupBlock({
  group,
  isActive,
  onItemClick,
}: {
  group: NavGroup;
  isActive: (href: string) => boolean;
  onItemClick: () => void;
}) {
  const t = useT("nav");
  const hasActiveItem = group.items.some((item) => isActive(item.href));
  // El grupo arranca abierto si contiene la ruta activa; tras montar se
  // reconcilia con la preferencia persistida en localStorage (si existe).
  const [open, setOpen] = useState(hasActiveItem);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // El grupo con la ruta activa siempre se auto-expande al cargar.
    if (hasActiveItem) {
      setOpen(true);
      return;
    }
    const stored = window.localStorage.getItem(LS_KEY_PREFIX + group.id);
    if (stored !== null) setOpen(stored === "1");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group.id, hasActiveItem]);

  const toggle = () => {
    setOpen((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(LS_KEY_PREFIX + group.id, next ? "1" : "0");
      }
      return next;
    });
  };

  const { Icon } = group;

  return (
    <li>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        data-testid={`nav-group-${group.id}`}
        className={cn(
          "text-sidebar-muted-foreground hover:text-sidebar-foreground",
          "flex w-full items-center gap-2 rounded-md px-3 py-2 text-xs font-semibold uppercase tracking-wider",
          "transition-colors",
        )}
      >
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="flex-1 text-left">{t(group.labelKey)}</span>
        <ChevronDown
          aria-hidden="true"
          className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open ? "" : "-rotate-90")}
        />
      </button>

      {open && (
        // Hijos indentados + guía vertical de árbol bajo la cabecera del grupo,
        // para que la jerarquía padre→hijo se distinga de un vistazo.
        <ul className="border-sidebar-border ml-3 mt-1 flex flex-col gap-1 border-l pl-2">
          {group.items.map(({ href, labelKey, Icon: ItemIcon }) => (
            <li key={href}>
              <NavLink
                href={href}
                labelKey={labelKey}
                Icon={ItemIcon}
                active={isActive(href)}
                onClick={onItemClick}
              />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * Un enlace del menú. Compartido con la barra del proyecto, que NO agrupa: sus
 * once entradas son una lista plana y usan el mismo aspecto y el mismo
 * `data-testid` que las de un grupo.
 */
export function NavLink({
  href,
  labelKey,
  Icon,
  active,
  onClick,
  testId,
}: {
  href: string;
  labelKey: NavGroup["items"][number]["labelKey"];
  Icon: NavGroup["Icon"];
  active: boolean;
  onClick: () => void;
  /**
   * Por defecto `nav-${último-segmento}`, que es el que ya usan los e2e. La
   * barra del proyecto lo pasa explícito: su último segmento es el UUID del
   * proyecto, así que el `data-testid` derivado cambiaría con cada proyecto y
   * ningún test podría apuntarlo.
   */
  testId?: string;
}) {
  const t = useT("nav");
  return (
    <Link
      href={href}
      onClick={onClick}
      className={cn(
        "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
        "transition-colors",
        active
          ? "text-sidebar-active bg-[hsl(var(--sidebar-active-bg))]"
          : "text-sidebar-muted-foreground hover:bg-sidebar-border hover:text-sidebar-foreground",
      )}
      data-testid={testId ?? `nav-${href.split("/").pop()}`}
      aria-current={active ? "page" : undefined}
    >
      {/* Active indicator: thin gradient stripe on the left */}
      {active && (
        <span
          aria-hidden="true"
          className="bg-brand-gradient absolute left-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-r"
        />
      )}
      <Icon className="h-4 w-4 shrink-0" />
      <span>{t(labelKey)}</span>
    </Link>
  );
}
