"use client";

/**
 * El marco común de las tres barras laterales: marca, cierre en móvil y el
 * `<nav>` scrollable donde cada área pinta lo suyo.
 *
 * `task_ui_01`. Existe para que las tres barras no repitan la cabecera —y para
 * que `data-testid="sidebar-nav"`, del que cuelgan los e2e, siga siendo uno y
 * el mismo en las tres.
 */

import type { ReactNode } from "react";
import Link from "next/link";
import { Sparkles, X } from "lucide-react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function SidebarFrame({
  children,
  onItemClick,
  showClose = false,
  onClose,
  area,
}: {
  children: ReactNode;
  onItemClick: () => void;
  showClose?: boolean;
  onClose?: () => void;
  /** Va al `data-area` del `<nav>`: es lo que un test lee para saber qué barra se pintó. */
  area: string;
}) {
  const t = useT("shell");

  return (
    <>
      <div className="border-sidebar-border flex h-20 items-center justify-between border-b px-6">
        <Link
          href="/admin/dashboard"
          className="text-sidebar-foreground flex items-center gap-2 font-semibold tracking-tight"
          onClick={onItemClick}
        >
          <span
            className={cn(
              "bg-brand-gradient inline-flex h-7 w-7 items-center justify-center rounded-md",
              "shadow-[0_0_24px_-4px_hsl(var(--gradient-from)/0.7)]",
            )}
          >
            <Sparkles className="h-4 w-4 text-white" />
          </span>
          <span>Agentic Platform</span>
        </Link>
        {showClose && (
          <button
            type="button"
            onClick={onClose}
            className="text-sidebar-muted-foreground hover:bg-sidebar-border hover:text-sidebar-foreground inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors"
            aria-label={t("closeMenu")}
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <nav
        className="scrollbar-thin flex-1 overflow-y-auto px-3 py-4"
        data-testid="sidebar-nav"
        data-area={area}
      >
        {children}
      </nav>
    </>
  );
}
