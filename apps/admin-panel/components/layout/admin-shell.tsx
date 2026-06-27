"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Bell,
  BellRing,
  BookOpen,
  Bot,
  Brain,
  Briefcase,
  ChevronDown,
  Coins,
  Cpu,
  DatabaseBackup,
  FileText,
  FolderKanban,
  Gauge,
  HelpCircle,
  Inbox,
  KeyRound,
  LayoutDashboard,
  LayoutGrid,
  Library,
  ListChecks,
  Server,
  Settings,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Store,
  UserRound,
  Users,
  Wrench,
  X,
} from "lucide-react";

import { AdminHeader } from "@/components/layout/admin-header";
import { GlobalProgress } from "@/components/layout/global-progress";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/lib/use-current-user";

export interface NavItem {
  href: string;
  label: string;
  Icon: typeof LayoutDashboard;
  /** Si `adminOnly`, sólo se muestra a tenant_admin / system_admin. */
  adminOnly?: boolean;
  /** Si `systemAdminOnly`, sólo se muestra al System Admin global. */
  systemAdminOnly?: boolean;
  /** Si `systemOwnerOnly`, sólo se muestra al System Owner (córtex F1, ADR 0074). */
  systemOwnerOnly?: boolean;
}

export interface NavGroup {
  /** Identificador estable: clave de localStorage + `data-testid`. */
  id: string;
  label: string;
  Icon: typeof LayoutDashboard;
  items: NavItem[];
  /** Ámbito del grupo entero (RBAC + ADR 0028). */
  adminOnly?: boolean;
  systemAdminOnly?: boolean;
  /** Ámbito de grupo reservado al System Owner (córtex F1). */
  systemOwnerOnly?: boolean;
}

/** Predicados de rol que deciden la visibilidad de un ítem/grupo del NAV. */
export interface NavScope {
  isTenantAdmin: boolean;
  isSystemAdmin: boolean;
  isSystemOwner: boolean;
}

/**
 * ¿Visible este ítem para el rol actual? Lógica pura, factorizada fuera del
 * componente para poder testearla sin renderizar React (vitest env `node`).
 * El gating más restrictivo manda; el backend sigue siendo la barrera real.
 */
export function navItemVisible(item: NavItem, scope: NavScope): boolean {
  if (item.systemOwnerOnly) return scope.isSystemOwner;
  if (item.systemAdminOnly) return scope.isSystemAdmin;
  if (item.adminOnly) return scope.isTenantAdmin;
  return true;
}

/** ¿Visible este grupo (por su propio ámbito) para el rol actual? */
export function navGroupVisible(group: NavGroup, scope: NavScope): boolean {
  if (group.systemOwnerOnly) return scope.isSystemOwner;
  if (group.systemAdminOnly) return scope.isSystemAdmin;
  if (group.adminOnly) return scope.isTenantAdmin;
  return true;
}

/**
 * Grupos visibles según el rol, con sus ítems ya filtrados por gating de ítem
 * y descartando los grupos que se quedan sin ítems. Pura → testeable.
 */
export function visibleNavGroups(groups: NavGroup[], scope: NavScope): NavGroup[] {
  return groups
    .filter((group) => navGroupVisible(group, scope))
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => navItemVisible(item, scope)),
    }))
    .filter((group) => group.items.length > 0);
}

/**
 * Navegación en 5 grupos colapsables (Plan admin-menu-reorg task_menu_01).
 *
 * El orden y el ámbito son fijos (ver docs/03-guides/ui-conventions.md →
 * "Navegación del panel"). El ámbito de grupo (`adminOnly`/`systemAdminOnly`)
 * decide qué grupos ve cada rol; el gating por ítem se conserva además del de
 * grupo. La barrera real sigue siendo el backend — esto es UX.
 *
 * Las rutas (`href`) y los `data-testid` derivados (`nav-${último-segmento}`)
 * NO cambian: los e2e dependen de ellos.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "trabajo",
    label: "Trabajo",
    Icon: Briefcase,
    items: [
      { href: "/admin/dashboard", label: "Dashboard", Icon: LayoutDashboard },
      { href: "/admin/inbox", label: "Mis tareas", Icon: ListChecks },
      { href: "/admin/board", label: "Tablero", Icon: LayoutGrid },
      { href: "/admin/runs", label: "Runs", Icon: Activity },
      { href: "/admin/approvals", label: "Aprobaciones", Icon: BellRing },
      { href: "/admin/notifications/inbox", label: "Bandeja", Icon: Inbox },
      { href: "/admin/assistant", label: "Asistente", Icon: Bot, adminOnly: true },
    ],
  },
  {
    id: "recursos",
    label: "Recursos",
    Icon: Library,
    adminOnly: true,
    items: [
      { href: "/admin/agents", label: "Agentes", Icon: Bot },
      { href: "/admin/tools", label: "Catálogo", Icon: Wrench },
      { href: "/admin/human-agents", label: "Agentes humanos", Icon: UserRound, adminOnly: true },
      { href: "/admin/teams", label: "Equipos", Icon: Users },
      { href: "/admin/projects", label: "Proyectos", Icon: FolderKanban },
      { href: "/admin/knowledge-bases", label: "Knowledge Bases", Icon: Library },
      { href: "/admin/memories", label: "Memorias", Icon: Brain },
      { href: "/admin/documents", label: "Documentos", Icon: FileText },
    ],
  },
  {
    id: "config-tenant",
    label: "Configuración del tenant",
    Icon: SlidersHorizontal,
    adminOnly: true,
    items: [
      { href: "/admin/guardrails", label: "Guardrails", Icon: ShieldAlert, adminOnly: true },
      {
        href: "/admin/approval-policy",
        label: "Validación humana",
        Icon: ShieldCheck,
        adminOnly: true,
      },
      { href: "/admin/notifications", label: "Notificaciones", Icon: Bell, adminOnly: true },
      { href: "/admin/eval-quality", label: "Calidad (Evals)", Icon: Gauge, adminOnly: true },
      { href: "/admin/tenant-stats", label: "Estadísticas", Icon: BarChart3, adminOnly: true },
      { href: "/admin/marketplace", label: "Marketplace", Icon: Store, adminOnly: true },
      { href: "/admin/settings", label: "Settings", Icon: Settings, adminOnly: true },
    ],
  },
  {
    id: "plataforma",
    label: "Plataforma",
    Icon: Server,
    systemAdminOnly: true,
    items: [
      // Administración de usuarios global (ADR 0047): listar usuarios y
      // gestionar sus memberships (usuario↔tenant + rol). Solo System Admin.
      { href: "/admin/users", label: "Usuarios", Icon: Users, systemAdminOnly: true },
      { href: "/admin/llm-providers", label: "Proveedores LLM", Icon: Cpu, systemAdminOnly: true },
      {
        href: "/admin/ollama",
        label: "Ollama & Embeddings",
        Icon: Sparkles,
        systemAdminOnly: true,
      },
      {
        href: "/admin/settings/platform-defaults",
        label: "Valores por defecto",
        Icon: SlidersHorizontal,
        systemAdminOnly: true,
      },
      {
        href: "/admin/model-prices",
        label: "Modelos & Precios",
        Icon: Coins,
        systemAdminOnly: true,
      },
      // SSO recolocado de "Ajustes del tenant" → "Plataforma" (ADR 0028).
      // La ruta NO cambia (/admin/settings/sso); el backend de SSO sigue
      // siendo per-tenant (ADR 0031) — aquí solo cambia el sitio en el menú.
      { href: "/admin/settings/sso", label: "Auth/SSO", Icon: KeyRound, systemAdminOnly: true },
      { href: "/admin/backup", label: "Backups", Icon: DatabaseBackup, systemAdminOnly: true },
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
    ],
  },
  {
    // Córtex del System Owner (F1, ADR 0074). Grupo separado y reservado al
    // dueño del despliegue — el backend (require_system_owner, DB-authoritative)
    // sigue siendo la barrera real; esto es solo UX.
    id: "cortex",
    label: "Córtex",
    Icon: Brain,
    systemOwnerOnly: true,
    items: [
      { href: "/admin/cortex", label: "Córtex", Icon: Brain, systemOwnerOnly: true },
      // Panel de Mente (Córtex F2, ADR 0075): estado afectivo del córtex en vivo.
      {
        href: "/admin/cortex/mind",
        label: "Panel de Mente",
        Icon: Activity,
        systemOwnerOnly: true,
      },
      // Identidad evolutiva (Córtex F3, ADR 0074/0077): onboarding co-diseñado.
      {
        href: "/admin/cortex/identity",
        label: "Identidad",
        Icon: Sparkles,
        systemOwnerOnly: true,
      },
    ],
  },
  {
    id: "ayuda",
    label: "Ayuda",
    Icon: HelpCircle,
    items: [{ href: "/admin/docs", label: "Documentación", Icon: BookOpen }],
  },
];

const LS_KEY_PREFIX = "agentic.nav.group.";

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
      {/* `min-w-0`: un flex item arranca con `min-width:auto`, así que NO se encoge
          por debajo del min-content de su contenido. Sin esto, en cuanto una página
          tiene un descendiente ancho (una tabla, un grid…) la columna crece más que
          el viewport y aparece scroll horizontal de PÁGINA. Con min-w-0 la columna
          se ajusta al viewport y los contenedores overflow-x-auto de dentro hacen su
          propio scroll. Va en el shell (denominador común) para que valga en TODAS
          las páginas, no parcheando cada una. */}
      <div className="flex min-w-0 flex-1 flex-col md:pl-64">
        <AdminHeader onOpenMobileNav={() => setMobileOpen(true)} />
        <main className="animate-fade-in min-w-0 flex-1">{children}</main>
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
  // Córtex F1 (ADR 0074): grupo systemOwnerOnly visible solo al System Owner.
  // El check del backend sigue siendo la fuente de verdad — esto es UX.
  const { isTenantAdmin, isSystemAdmin, isSystemOwner } = useCurrentUser();

  // Grupos visibles por ámbito, con sus ítems ya filtrados por gating de ítem.
  const visibleGroups = visibleNavGroups(NAV_GROUPS, {
    isTenantAdmin,
    isSystemAdmin,
    isSystemOwner,
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

      <nav className="scrollbar-thin flex-1 overflow-y-auto px-3 py-4" data-testid="sidebar-nav">
        <ul className="flex flex-col gap-2">
          {visibleGroups.map((group) => (
            <NavGroupBlock
              key={group.id}
              group={group}
              isActive={isActive}
              onItemClick={onItemClick}
            />
          ))}
        </ul>
      </nav>
    </>
  );
}

function NavGroupBlock({
  group,
  isActive,
  onItemClick,
}: {
  group: NavGroup;
  isActive: (href: string) => boolean;
  onItemClick: () => void;
}) {
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
        <span className="flex-1 text-left">{group.label}</span>
        <ChevronDown
          aria-hidden="true"
          className={cn("h-3.5 w-3.5 shrink-0 transition-transform", open ? "" : "-rotate-90")}
        />
      </button>

      {open && (
        // Hijos indentados + guía vertical de árbol bajo la cabecera del grupo,
        // para que la jerarquía padre→hijo se distinga de un vistazo.
        <ul className="ml-3 mt-1 flex flex-col gap-1 border-l border-sidebar-border pl-2">
          {group.items.map(({ href, label, Icon: ItemIcon }) => {
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
                  <ItemIcon className="h-4 w-4 shrink-0" />
                  <span>{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}
