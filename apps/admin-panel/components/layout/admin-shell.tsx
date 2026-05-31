"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Bell,
  BellRing,
  BookOpen,
  Bot,
  Brain,
  Coins,
  Cpu,
  DatabaseBackup,
  FileText,
  FolderKanban,
  Gauge,
  Inbox,
  LayoutDashboard,
  LayoutGrid,
  Library,
  ListChecks,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Store,
  UserRound,
  Users,
  X,
} from "lucide-react";

import { AdminHeader } from "@/components/layout/admin-header";
import { GlobalProgress } from "@/components/layout/global-progress";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/lib/use-current-user";

interface NavItem {
  href: string;
  label: string;
  Icon: typeof LayoutDashboard;
  /** Si `adminOnly`, sólo se muestra a tenant_admin / system_admin. */
  adminOnly?: boolean;
  /** Si `systemAdminOnly`, sólo se muestra al System Admin global. */
  systemAdminOnly?: boolean;
}

const NAV: NavItem[] = [
  { href: "/admin/dashboard", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/admin/inbox", label: "Mis tareas", Icon: ListChecks },
  { href: "/admin/agents", label: "Agentes", Icon: Bot },
  { href: "/admin/human-agents", label: "Agentes humanos", Icon: UserRound, adminOnly: true },
  { href: "/admin/teams", label: "Equipos", Icon: Users },
  { href: "/admin/projects", label: "Proyectos", Icon: FolderKanban },
  { href: "/admin/board", label: "Tablero", Icon: LayoutGrid },
  { href: "/admin/approvals", label: "Aprobaciones", Icon: BellRing },
  {
    href: "/admin/approval-policy",
    label: "Validación humana",
    Icon: ShieldCheck,
    adminOnly: true,
  },
  { href: "/admin/memories", label: "Memorias", Icon: Brain },
  { href: "/admin/knowledge-bases", label: "Knowledge Bases", Icon: Library },
  { href: "/admin/marketplace", label: "Marketplace", Icon: Store, adminOnly: true },
  {
    href: "/admin/model-prices",
    label: "Modelos & Precios",
    Icon: Coins,
    systemAdminOnly: true,
  },
  {
    href: "/admin/llm-providers",
    label: "Proveedores LLM",
    Icon: Cpu,
    systemAdminOnly: true,
  },
  {
    href: "/admin/backup",
    label: "Backups",
    Icon: DatabaseBackup,
    systemAdminOnly: true,
  },
  {
    href: "/admin/backup/destinations",
    label: "Destinos backup",
    Icon: DatabaseBackup,
    systemAdminOnly: true,
  },
  {
    href: "/admin/backup/restore",
    label: "Restaurar backup",
    Icon: DatabaseBackup,
    systemAdminOnly: true,
  },
  {
    href: "/admin/guardrails",
    label: "Guardrails",
    Icon: ShieldAlert,
    adminOnly: true,
  },
  {
    href: "/admin/eval-quality",
    label: "Calidad (Evals)",
    Icon: Gauge,
    adminOnly: true,
  },
  {
    href: "/admin/tenant-stats",
    label: "Estadísticas",
    Icon: BarChart3,
    adminOnly: true,
  },
  {
    href: "/admin/notifications",
    label: "Notificaciones",
    Icon: Bell,
    adminOnly: true,
  },
  { href: "/admin/notifications/inbox", label: "Bandeja", Icon: Inbox },
  { href: "/admin/documents", label: "Documentos", Icon: FileText },
  { href: "/admin/docs", label: "Documentación", Icon: BookOpen },
  { href: "/admin/settings", label: "Settings", Icon: Settings, adminOnly: true },
];

export function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  const isActive = (href: string) => pathname === href || pathname?.startsWith(href + "/") === true;

  return (
    <div className="bg-background flex min-h-screen">
      <GlobalProgress />
      {/* ============================= Sidebar (desktop) ============================= */}
      <aside
        className={cn(
          "bg-sidebar text-sidebar-foreground fixed inset-y-0 left-0 z-40 w-64 flex-col",
          "border-sidebar-border border-r",
          "hidden md:flex",
        )}
      >
        <SidebarContent isActive={isActive} onItemClick={() => setMobileOpen(false)} />
      </aside>

      {/* ============================= Sidebar (mobile drawer) ============================= */}
      {mobileOpen && (
        <>
          <div
            className="bg-foreground/60 fixed inset-0 z-40 backdrop-blur-sm md:hidden"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
          <aside
            className={cn(
              "bg-sidebar text-sidebar-foreground border-sidebar-border",
              "fixed inset-y-0 left-0 z-50 flex w-72 flex-col border-r md:hidden",
            )}
            role="dialog"
            aria-modal="true"
            data-testid="mobile-nav"
          >
            <SidebarContent
              isActive={isActive}
              onItemClick={() => setMobileOpen(false)}
              showClose
              onClose={() => setMobileOpen(false)}
            />
          </aside>
        </>
      )}

      {/* ============================= Main column ============================= */}
      <div className="flex flex-1 flex-col md:pl-64">
        <AdminHeader onOpenMobileNav={() => setMobileOpen(true)} />
        <main className="animate-fade-in flex-1">{children}</main>
      </div>
    </div>
  );
}

function SidebarContent({
  isActive,
  onItemClick,
  showClose = false,
  onClose,
}: {
  isActive: (href: string) => boolean;
  onItemClick: () => void;
  showClose?: boolean;
  onClose?: () => void;
}) {
  // Plan 06.8 task_06_8_08: ocultar items admin-only para tenant_user.
  // El check del backend sigue siendo la fuente de verdad — esto es UX.
  const { isTenantAdmin, isSystemAdmin } = useCurrentUser();
  const visibleNav = NAV.filter((item) => {
    if (item.systemAdminOnly) return isSystemAdmin;
    if (item.adminOnly) return isTenantAdmin;
    return true;
  });

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
            aria-label="Cerrar menú"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4" data-testid="sidebar-nav">
        <p className="text-sidebar-muted-foreground mb-2 px-3 text-xs font-semibold uppercase tracking-wider">
          Workspace
        </p>
        <ul className="flex flex-col gap-1">
          {visibleNav.map(({ href, label, Icon }) => {
            const active = isActive(href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  onClick={onItemClick}
                  className={cn(
                    "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
                    "transition-colors",
                    active
                      ? "bg-[hsl(var(--sidebar-active-bg))] text-sidebar-active"
                      : "text-sidebar-muted-foreground hover:bg-sidebar-border hover:text-sidebar-foreground",
                  )}
                  data-testid={`nav-${href.split("/").pop()}`}
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
                  <span>{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
