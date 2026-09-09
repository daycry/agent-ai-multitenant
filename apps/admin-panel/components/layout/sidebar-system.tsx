"use client";

/**
 * La barra lateral del área **Sistema**: la plataforma, no el tenant.
 *
 * `task_ui_01` + ADR 0117 (c). Antes estos dos grupos —Plataforma y Córtex—
 * colgaban del mismo menú que el trabajo del tenant, así que un System Admin
 * veía en una sola lista «Aprobaciones» y «Restaurar backup». Separarlos es el
 * cambio; el contenido de cada grupo, su orden y sus `href` se conservan.
 *
 * El grupo `plataforma` es `systemAdminOnly` y `cortex` es `systemOwnerOnly`
 * (ADR 0074): el gating por grupo se conserva TAL CUAL además del gating del
 * área. Que el área ya sea de System Admin no hace redundante el del grupo — un
 * System Owner no tiene por qué ser System Admin, y `visibleNavGroups` es lo que
 * decide qué ve cada uno dentro.
 *
 * `/admin/docs` aparece aquí **y** en Trabajo, a propósito: el plan lista la
 * documentación en esta área, y quitarla de la otra se la retiraría a todo el
 * que no sea System Admin.
 */

import {
  Activity,
  BookOpen,
  Brain,
  ClipboardCheck,
  Coins,
  Cpu,
  DatabaseBackup,
  HelpCircle,
  KeyRound,
  Server,
  SlidersHorizontal,
  Sparkles,
  Ticket,
  Users,
} from "lucide-react";

import { NavGroupBlock } from "./nav-group-block";
import { visibleNavGroups, type NavGroup, type NavScope } from "./nav-model";
import { SidebarFrame } from "./sidebar-frame";

/** Los grupos del área Sistema, en su orden. */
export const SYSTEM_GROUPS: NavGroup[] = [
  {
    id: "plataforma",
    labelKey: "groupPlataforma",
    Icon: Server,
    systemAdminOnly: true,
    items: [
      // Administración de usuarios global (ADR 0047): listar usuarios y
      // gestionar sus memberships (usuario↔tenant + rol). Solo System Admin.
      { href: "/admin/users", labelKey: "users", Icon: Users, systemAdminOnly: true },
      // ADR 0134: con el registro público cerrado, ésta es la ÚNICA vía de
      // producto para dar de alta a alguien nuevo. Sin entrada en el menú, la
      // pantalla existiría y nadie la encontraría.
      { href: "/admin/invitations", labelKey: "invitations", Icon: Ticket, systemAdminOnly: true },
      // `task_mk_13` (UI-05): la cola de revisión del marketplace (ADR 0142 D6) es
      // del System Admin. Sin entrada aquí, publicar dejaba el listing en
      // `pending` y nadie sabía dónde se aprobaba.
      {
        href: "/admin/marketplace/review",
        labelKey: "marketplaceReview",
        Icon: ClipboardCheck,
        systemAdminOnly: true,
      },
      { href: "/admin/llm-providers", labelKey: "llmProviders", Icon: Cpu, systemAdminOnly: true },
      {
        href: "/admin/ollama",
        labelKey: "ollama",
        Icon: Sparkles,
        systemAdminOnly: true,
      },
      {
        href: "/admin/settings/platform-defaults",
        labelKey: "platformDefaults",
        Icon: SlidersHorizontal,
        systemAdminOnly: true,
      },
      {
        href: "/admin/model-prices",
        labelKey: "modelPrices",
        Icon: Coins,
        systemAdminOnly: true,
      },
      // SSO recolocado de "Ajustes del tenant" → "Plataforma" (ADR 0028).
      // La ruta NO cambia (/admin/settings/sso); el backend de SSO sigue
      // siendo per-tenant (ADR 0031) — aquí solo cambia el sitio en el menú.
      { href: "/admin/settings/sso", labelKey: "sso", Icon: KeyRound, systemAdminOnly: true },
      { href: "/admin/backup", labelKey: "backup", Icon: DatabaseBackup, systemAdminOnly: true },
      {
        href: "/admin/backup/destinations",
        labelKey: "backupDestinations",
        Icon: DatabaseBackup,
        systemAdminOnly: true,
      },
      {
        href: "/admin/backup/restore",
        labelKey: "backupRestore",
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
    labelKey: "groupCortex",
    Icon: Brain,
    systemOwnerOnly: true,
    items: [
      { href: "/admin/cortex", labelKey: "cortex", Icon: Brain, systemOwnerOnly: true },
      // Panel de Mente (Córtex F2, ADR 0075): estado afectivo del córtex en vivo.
      {
        href: "/admin/cortex/mind",
        labelKey: "cortexMind",
        Icon: Activity,
        systemOwnerOnly: true,
      },
      // Identidad evolutiva (Córtex F3, ADR 0074/0077): onboarding co-diseñado.
      {
        href: "/admin/cortex/identity",
        labelKey: "cortexIdentity",
        Icon: Sparkles,
        systemOwnerOnly: true,
      },
    ],
  },
  {
    id: "ayuda-sistema",
    labelKey: "groupAyuda",
    Icon: HelpCircle,
    items: [{ href: "/admin/docs", labelKey: "docs", Icon: BookOpen }],
  },
];

export function SidebarSystem({
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
  const groups = visibleNavGroups(SYSTEM_GROUPS, scope);

  return (
    <SidebarFrame area="system" onItemClick={onItemClick} showClose={showClose} onClose={onClose}>
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
