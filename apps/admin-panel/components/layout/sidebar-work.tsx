"use client";

/**
 * La barra lateral del área **Trabajo**: el tenant.
 *
 * `task_ui_01`. Se queda con cuatro de los seis grupos que tenía el menú único
 * —Trabajo, Recursos, Configuración del tenant y Ayuda—; Plataforma y Córtex se
 * van al área Sistema (ADR 0117 c), que es lo que este plan viene a separar.
 *
 * **El orden y los `href` NO cambian**, ni los `data-testid` derivados: los
 * grupos son los mismos objetos que antes vivían en `NAV_GROUPS` y los e2e
 * dependen de ellos. Lo único que cambia es en qué barra se pintan.
 *
 * Dos cosas que este fichero conserva a propósito y conviene no "limpiar":
 *
 * - **`/admin/runs` sigue en el menú.** El plan lo retira («se consulta desde
 *   el proyecto o el dashboard»), pero la pestaña Runs del proyecto la cablea
 *   `task_ui_12` y el dashboard que enlaza runs, `task_ui_02`. Retirarlo aquí
 *   dejaría la ruta viva y sin camino durante media ola. Lo quita `task_ui_02`.
 * - **`/admin/docs` sigue en Ayuda.** El plan lo lista en el área Sistema, pero
 *   ese área es sólo del System Admin: moverlo del todo le quitaría la
 *   documentación a todos los demás. Está en los dos sitios y no fuerza área
 *   (ver `SHARED_ROUTES` en `nav-model.ts`).
 *
 * Y una que SÍ cambia: **`/admin/settings/security` se va de aquí** al menú de
 * usuario de la cabecera (decisión del operador del 2026-09-09, §Preguntas
 * abiertas del mapa de pantallas). Es la verificación en dos pasos de la propia
 * cuenta —un ajuste personal—, y en el grupo «Trabajo» quedaba mezclada con el
 * trabajo del tenant. La ruta no cambia.
 */

import {
  BarChart3,
  Bell,
  BellRing,
  BookOpen,
  Bot,
  Briefcase,
  Building2,
  DoorOpen,
  FileText,
  FolderKanban,
  Gauge,
  HelpCircle,
  Inbox,
  LayoutDashboard,
  LayoutGrid,
  Library,
  ListChecks,
  Settings,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Store,
  Trophy,
  UserRound,
  Users,
  Wrench,
  Activity,
  Brain,
} from "lucide-react";

import { NavGroupBlock } from "./nav-group-block";
import { visibleNavGroups, type NavGroup, type NavScope } from "./nav-model";
import { SidebarFrame } from "./sidebar-frame";

/** Los cuatro grupos del área Trabajo, en su orden. */
export const WORK_GROUPS: NavGroup[] = [
  {
    id: "trabajo",
    labelKey: "groupTrabajo",
    Icon: Briefcase,
    items: [
      { href: "/admin/dashboard", labelKey: "dashboard", Icon: LayoutDashboard },
      { href: "/admin/inbox", labelKey: "inbox", Icon: ListChecks },
      // ADR 0123: todo lo que espera decisión humana, por antigüedad.
      { href: "/admin/human-queue", labelKey: "humanQueue", Icon: DoorOpen },
      { href: "/admin/board", labelKey: "board", Icon: LayoutGrid },
      // ADR 0118: el tenant en vivo como piso 2D sobre telemetría real.
      { href: "/admin/office", labelKey: "office", Icon: Building2 },
      { href: "/admin/runs", labelKey: "runs", Icon: Activity },
      // ADR 0121: ranking modelo×agente con la carga real del tenant.
      { href: "/admin/leaderboard", labelKey: "leaderboard", Icon: Trophy },
      { href: "/admin/approvals", labelKey: "approvals", Icon: BellRing },
      { href: "/admin/notifications/inbox", labelKey: "notificationsInbox", Icon: Inbox },
      { href: "/admin/assistant", labelKey: "assistant", Icon: Bot, adminOnly: true },
    ],
  },
  {
    id: "recursos",
    labelKey: "groupRecursos",
    Icon: Library,
    adminOnly: true,
    items: [
      { href: "/admin/agents", labelKey: "agents", Icon: Bot },
      { href: "/admin/tools", labelKey: "tools", Icon: Wrench },
      { href: "/admin/human-agents", labelKey: "humanAgents", Icon: UserRound, adminOnly: true },
      { href: "/admin/teams", labelKey: "teams", Icon: Users },
      { href: "/admin/projects", labelKey: "projects", Icon: FolderKanban },
      { href: "/admin/knowledge-bases", labelKey: "knowledgeBases", Icon: Library },
      { href: "/admin/memories", labelKey: "memories", Icon: Brain },
      { href: "/admin/documents", labelKey: "documents", Icon: FileText },
    ],
  },
  {
    id: "config-tenant",
    labelKey: "groupConfigTenant",
    Icon: SlidersHorizontal,
    adminOnly: true,
    items: [
      { href: "/admin/guardrails", labelKey: "guardrails", Icon: ShieldAlert, adminOnly: true },
      {
        href: "/admin/approval-policy",
        labelKey: "approvalPolicy",
        Icon: ShieldCheck,
        adminOnly: true,
      },
      { href: "/admin/notifications", labelKey: "notifications", Icon: Bell, adminOnly: true },
      { href: "/admin/eval-quality", labelKey: "evalQuality", Icon: Gauge, adminOnly: true },
      { href: "/admin/tenant-stats", labelKey: "tenantStats", Icon: BarChart3, adminOnly: true },
      { href: "/admin/marketplace", labelKey: "marketplace", Icon: Store, adminOnly: true },
      { href: "/admin/settings", labelKey: "settings", Icon: Settings, adminOnly: true },
    ],
  },
  {
    id: "ayuda",
    labelKey: "groupAyuda",
    Icon: HelpCircle,
    items: [{ href: "/admin/docs", labelKey: "docs", Icon: BookOpen }],
  },
];

export function SidebarWork({
  scope,
  isActive,
  onItemClick,
  showClose = false,
  onClose,
}: {
  scope: NavScope;
  isActive: (href: string) => boolean;
  onItemClick: () => void;
  showClose?: boolean;
  onClose?: () => void;
}) {
  const groups = visibleNavGroups(WORK_GROUPS, scope);

  return (
    <SidebarFrame area="work" onItemClick={onItemClick} showClose={showClose} onClose={onClose}>
      <ul className="flex flex-col gap-2">
        {groups.map((group) => (
          <NavGroupBlock
            key={group.id}
            group={group}
            isActive={isActive}
            onItemClick={onItemClick}
          />
        ))}
      </ul>
    </SidebarFrame>
  );
}
