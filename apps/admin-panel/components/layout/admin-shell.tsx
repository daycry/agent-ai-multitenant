"use client";

/**
 * El shell de `/admin/*`: cabecera, columna principal y **la barra lateral que
 * toque**.
 *
 * Hasta `task_ui_01` este fichero tenía 522 líneas y lo hacía todo: los tipos
 * del NAV, el gating por rol, los seis grupos del menú, el render del grupo
 * colapsable y el layout. Era el fichero más acoplado del panel, y el plan
 * `ui-reestructuracion-2026-09-09` empieza partiéndolo porque todo lo demás
 * pasa por aquí.
 *
 * Dónde vive ahora cada cosa:
 *
 * | Antes, aquí                        | Ahora                        |
 * | ---------------------------------- | ---------------------------- |
 * | tipos + gating por rol             | `nav-model.ts`               |
 * | las tres áreas y su ruta           | `nav-model.ts`               |
 * | los grupos de Trabajo              | `sidebar-work.tsx`           |
 * | los grupos de Plataforma y Córtex  | `sidebar-system.tsx`         |
 * | (nuevo) las pestañas del proyecto  | `sidebar-project.tsx`        |
 * | marca + `<nav>` de la barra        | `sidebar-frame.tsx`          |
 * | grupo colapsable + enlace          | `nav-group-block.tsx`        |
 * | selector de área                   | `area-switcher.tsx`          |
 *
 * **Re-exporta el modelo** (`NAV_GROUPS`, los predicados y los tipos) porque
 * cuatro ficheros de test importan de aquí y su ruta de import no es lo que este
 * cambio venía a mover. `NAV_GROUPS` sigue siendo la lista COMPLETA —Trabajo
 * seguido de Sistema—, que es lo que esos tests afirman.
 *
 * ## El área activa: la ruta manda, el selector recuerda
 *
 * El área sale de `areaForPath(pathname)`; cuando la ruta no exige ninguna
 * (`/admin/docs`) se queda la última elegida. Así se cumple la regla del plan
 * —«un enlace a una ruta de Sistema desde Trabajo abre el área Sistema»— sin que
 * las rutas compartidas te saquen del sitio donde estabas. Pulsar en el selector
 * navega al inicio del área, porque cambiar de barra sin cambiar de pantalla
 * deja al usuario mirando una página que su barra nueva no contiene.
 */

import { useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";

import { AdminHeader } from "@/components/layout/admin-header";
import { GlobalProgress } from "@/components/layout/global-progress";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/lib/use-current-user";

import {
  areaForPath,
  projectIdFromPath,
  visibleAreas,
  type Area,
  type NavScope,
} from "./nav-model";
import { SidebarProject } from "./sidebar-project";
import { SidebarSystem, SYSTEM_GROUPS } from "./sidebar-system";
import { SidebarWork, WORK_GROUPS } from "./sidebar-work";

export {
  navGroupVisible,
  navItemVisible,
  visibleNavGroups,
  areaForPath,
  projectIdFromPath,
  projectNavItems,
  visibleAreas,
} from "./nav-model";
export type { Area, NavGroup, NavItem, NavKey, NavScope } from "./nav-model";
export { WORK_GROUPS } from "./sidebar-work";
export { SYSTEM_GROUPS } from "./sidebar-system";

/**
 * El NAV completo, en el orden en que estaba antes del troceo: los cuatro
 * grupos de Trabajo y después los de Sistema.
 *
 * Se conserva porque es la lista sobre la que afirman `admin-shell-rbac`,
 * `-cortex` y `-runs`: «ningún ítem admin-only se le escapa a un tenant_user»
 * es una propiedad del NAV entero, no de una barra.
 */
export const NAV_GROUPS = [...WORK_GROUPS, ...SYSTEM_GROUPS];

/** El destino al pulsar un área en el selector. */
const AREA_HOME: Record<"work" | "system", string> = {
  work: "/admin/dashboard",
  system: "/admin/users",
};

export function AdminShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { isTenantAdmin, isSystemAdmin, isSystemOwner } = useCurrentUser();
  const scope: NavScope = { isTenantAdmin, isSystemAdmin, isSystemOwner };

  // El área que exige la ruta, o la última elegida si a la ruta le da igual.
  //
  // Y una caída a Trabajo que no es decorativa: quien pega `/admin/users` en la
  // barra de direcciones sin ser System Admin va a recibir un 403 del backend,
  // pero de camino no puede quedarse con una barra lateral EN BLANCO —el área
  // Sistema no le enseña ningún grupo—. Se le da la que sí puede usar.
  const requested = areaForPath(pathname);
  const allowed = visibleAreas(scope);
  const required =
    requested === "system" && !allowed.includes("system") ? "work" : requested;

  const [sticky, setSticky] = useState<Area>(required ?? "work");
  useEffect(() => {
    if (required !== null && required !== "project") setSticky(required);
  }, [required]);
  const area: Area = required ?? sticky;

  const projectId = projectIdFromPath(pathname);
  const isActive = (href: string) => pathname === href || pathname?.startsWith(href + "/") === true;
  const closeMobile = () => setMobileOpen(false);

  const onSelectArea = (next: Area) => {
    if (next === area) return;
    setSticky(next === "project" ? "work" : next);
    router.push(AREA_HOME[next === "project" ? "work" : next]);
  };

  const sidebar = (showClose: boolean) => {
    const shared = {
      onItemClick: closeMobile,
      showClose,
      onClose: closeMobile,
    };
    if (area === "project" && projectId !== null) {
      return <SidebarProject projectId={projectId} pathname={pathname} {...shared} />;
    }
    if (area === "system") {
      return <SidebarSystem scope={scope} isActive={isActive} {...shared} />;
    }
    return <SidebarWork scope={scope} isActive={isActive} {...shared} />;
  };

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
        {sidebar(false)}
      </aside>

      {/* ============================= Sidebar (mobile drawer) ============================= */}
      {mobileOpen && (
        <>
          <div
            className="bg-foreground/60 fixed inset-0 z-40 backdrop-blur-sm md:hidden"
            onClick={closeMobile}
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
            {sidebar(true)}
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
        <AdminHeader
          onOpenMobileNav={() => setMobileOpen(true)}
          area={area}
          onSelectArea={onSelectArea}
        />
        <main className="animate-fade-in min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
