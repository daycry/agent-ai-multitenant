/**
 * El modelo de navegación del panel: tipos, gating por rol y las tres áreas.
 *
 * Plan `ui-reestructuracion-2026-09-09`, `task_ui_01`. Antes esto vivía dentro
 * de `admin-shell.tsx` junto al render; sale aquí porque el shell pasa a elegir
 * entre TRES barras laterales y la decisión de «cuál toca» es lógica pura que
 * conviene poder probar sin montar React.
 *
 * ## Por qué un módulo aparte y no dentro de `area-switcher.tsx`
 *
 * `sidebar-work.tsx` y `sidebar-system.tsx` necesitan estos tipos, y
 * `admin-shell.tsx` necesita las dos listas de grupos para seguir exportando
 * `NAV_GROUPS`. Si los tipos viviesen en cualquiera de esos ficheros el grafo de
 * imports se cerraría en ciclo. Un módulo hoja sin React lo evita, y de paso los
 * tests corren en entorno `node`.
 *
 * `admin-shell.tsx` RE-EXPORTA todo lo público de aquí: cuatro ficheros de test
 * (`admin-shell-nav-groups`, `-rbac`, `-cortex`, `-runs`) importan de él y su
 * ruta de import no es lo que este cambio venía a mover.
 *
 * El gating de este fichero es **UX**: la barrera real es el backend (RBAC +
 * RLS). Un menú que esconde algo no lo protege.
 */

import {
  BookOpen,
  Bot,
  Brain,
  Database,
  Library,
  ListChecks,
  MessagesSquare,
  Server,
  Webhook,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { MessageKey } from "@/lib/i18n";

/** Las claves válidas del namespace `nav`: un typo no compila. */
export type NavKey = MessageKey<"nav">;

export interface NavItem {
  href: string;
  /** Clave del namespace `nav` del diccionario (NO el texto). */
  labelKey: NavKey;
  Icon: LucideIcon;
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
  /** Clave del namespace `nav` del diccionario (NO el texto). */
  labelKey: NavKey;
  Icon: LucideIcon;
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

// ---------------------------------------------------------------------------
// Las tres áreas
// ---------------------------------------------------------------------------

/**
 * `work` = el tenant · `project` = dentro de un proyecto · `system` = plataforma.
 *
 * `project` no está en el selector a propósito: no se elige, se entra en él
 * desde el portfolio y se sale con «← Proyectos». Una pestaña «Proyecto» en el
 * selector obligaría a inventar «¿qué proyecto?» al pulsarla.
 */
export type Area = "work" | "project" | "system";

/**
 * Las rutas EXCLUSIVAS del área Sistema, por prefijo de segmento.
 *
 * Es la fuente única de la frontera, y no se deriva de `SYSTEM_GROUPS` para no
 * cerrar un ciclo de imports. Que las dos listas no se separen lo comprueba
 * `sidebar-system.test.ts`: toda ruta del sidebar de Sistema tiene que caer en
 * área `system`, porque si no, pulsarla cambiaría la barra bajo los pies del
 * usuario.
 *
 * El emparejamiento es por SEGMENTO, no `startsWith` a pelo: hay tres pares de
 * rutas hermanas repartidas entre dos áreas —`/admin/settings` (tenant) contra
 * `/admin/settings/sso` y `/admin/settings/platform-defaults` (plataforma), y
 * `/admin/marketplace` (tenant) contra `/admin/marketplace/review` (cola de
 * revisión del System Admin)—. Un `startsWith` se llevaría por delante los
 * ajustes del tenant enteros.
 */
const SYSTEM_ROUTES: readonly string[] = [
  "/admin/users",
  "/admin/invitations",
  "/admin/llm-providers",
  "/admin/ollama",
  "/admin/model-prices",
  "/admin/settings/platform-defaults",
  "/admin/settings/sso",
  "/admin/backup",
  "/admin/marketplace/review",
  "/admin/cortex",
];

/**
 * Rutas que viven en las DOS áreas y por eso no fuerzan ninguna.
 *
 * `/admin/docs` es el visor de documentación: el plan lo lista en el área
 * Sistema y hoy cuelga del grupo «Ayuda», que ve cualquiera. Se queda en los dos
 * sitios —quitarlo de Trabajo le retiraría la documentación a todo el que no sea
 * System Admin, que no es lo que el plan pide— y no fuerza área para que entrar
 * en él no te eche del sitio donde estabas.
 */
const SHARED_ROUTES: readonly string[] = ["/admin/docs"];

function matchesRoute(pathname: string, route: string): boolean {
  return pathname === route || pathname.startsWith(route + "/");
}

/**
 * El área que EXIGE una ruta, o `null` si le da igual.
 *
 * `null` no es «no sé»: es «quédate donde estás». Lo devuelven las rutas
 * compartidas y todo lo que cae fuera de `/admin`.
 */
export function areaForPath(pathname: string | null | undefined): Area | null {
  if (!pathname || !pathname.startsWith("/admin")) return null;
  if (SHARED_ROUTES.some((route) => matchesRoute(pathname, route))) return null;
  if (SYSTEM_ROUTES.some((route) => matchesRoute(pathname, route))) return "system";
  if (projectIdFromPath(pathname) !== null) return "project";
  return "work";
}

/** Las áreas que el selector ofrece a este rol. Trabajo siempre; Sistema, si toca. */
export function visibleAreas(scope: NavScope): Area[] {
  return scope.isSystemAdmin || scope.isSystemOwner ? ["work", "system"] : ["work"];
}

/**
 * El id del proyecto de una ruta, o `null` si la ruta no está dentro de uno.
 *
 * `/admin/projects` (portfolio) y `/admin/projects/new` (alta) son del tenant:
 * su barra es la de Trabajo. Si `new` devolviera id, el sidebar del proyecto
 * pintaría ocho enlaces a `/admin/projects/new/...`.
 */
export function projectIdFromPath(pathname: string | null | undefined): string | null {
  if (!pathname) return null;
  const match = /^\/admin\/projects\/([^/]+)/.exec(pathname);
  if (match === null) return null;
  const id = match[1];
  return id === "new" ? null : id;
}

/**
 * La barra contextual de un proyecto, acotada a las pestañas que EXISTEN hoy.
 *
 * Las que el plan añade —Tablero (`task_ui_11`), Equipo y Costes
 * (`task_ui_12`)— no se anuncian aquí hasta que su `page.tsx` exista: una
 * entrada de menú a una ruta que nadie sirve es un 404 con aspecto de producto,
 * y la guarda `tests/unit/test_admin_panel_routes_preserved.py` fija cuáles hay.
 */
export function projectNavItems(projectId: string): NavItem[] {
  const at = (suffix: string) => `/admin/projects/${projectId}${suffix}`;
  return [
    { href: at(""), labelKey: "projectOverview", Icon: Library },
    { href: at("/plans"), labelKey: "projectPlans", Icon: ListChecks },
    { href: at("/tasks"), labelKey: "projectTasks", Icon: ListChecks },
    { href: at("/chat"), labelKey: "projectChat", Icon: MessagesSquare },
    { href: at("/mcp-servers"), labelKey: "projectCapabilities", Icon: Server },
    { href: at("/agent-tools-diagnostic"), labelKey: "projectToolsDiagnostic", Icon: Wrench },
    { href: at("/commands"), labelKey: "projectCommands", Icon: Bot },
    { href: at("/knowledge-bases"), labelKey: "projectKnowledge", Icon: BookOpen },
    { href: at("/memories"), labelKey: "projectMemories", Icon: Brain },
    { href: at("/incoming-webhooks"), labelKey: "projectWebhooks", Icon: Webhook },
    { href: at("/dep-cache"), labelKey: "projectDepCache", Icon: Database },
  ];
}
