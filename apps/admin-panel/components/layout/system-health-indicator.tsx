"use client";

/**
 * El indicador de salud de la cabecera: **sólo aparece si algo duele**.
 *
 * `task_ui_01`. El dashboard de hoy dedica su parte de arriba a una rejilla con
 * el estado de once servicios, casi siempre toda verde: contesta «¿está vivo el
 * stack?» a alguien que entró a preguntar «¿qué me espera hoy?». La decisión 3
 * del plan lo invierte — la salud pasa a la cabecera y sólo cuando está
 * degradada—, y esta pieza es esa mitad.
 *
 * Dos restricciones que no son estéticas:
 *
 * 1. **Sólo lo consulta el System Admin.** `/admin/system-health` depende de
 *    `require_system_admin` en el backend: pedirlo para un tenant_admin serían
 *    403 en bucle en cada carga de página.
 * 2. **Sólo pinta si hay algo que no está `ok`.** Un indicador verde permanente
 *    es ruido que se aprende a ignorar, y el día que se pone rojo ya nadie lo
 *    mira.
 *
 * Comparte la `queryKey` con el dashboard (`["system-health"]`), así que las dos
 * pantallas se sirven de una sola petición mientras el dato está fresco.
 *
 * **Adónde enlaza, hoy**: a `/admin/dashboard`, que es donde vive la rejilla con
 * el detalle por servicio. Cuando `task_ui_02` reescriba el dashboard y saque la
 * salud a su pantalla del área Sistema, este enlace la sigue.
 */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface ServiceHealth {
  name: string;
  status: "ok" | "degraded" | "down" | string;
  detail?: string | null;
}

export interface SystemHealthResponse {
  status: string;
  services: ServiceHealth[];
}

/**
 * Los servicios que NO están `ok`.
 *
 * Pura y exportada para poder probarla sin red: la decisión de «esto duele» es
 * lo único que este componente aporta, y afirmarla sobre el render obligaría a
 * montar TanStack Query en el test.
 */
export function degradedServices(health: SystemHealthResponse | undefined): ServiceHealth[] {
  if (!health?.services) return [];
  return health.services.filter((service) => service.status !== "ok");
}

export function SystemHealthIndicator({ isSystemAdmin }: { isSystemAdmin: boolean }) {
  const t = useT("shell");

  const { data } = useQuery({
    queryKey: ["system-health"],
    queryFn: () => apiFetch<SystemHealthResponse>("/admin/system-health"),
    enabled: isSystemAdmin,
    staleTime: 30_000,
    refetchInterval: 60_000,
    // Un fallo de la sonda no debe llenar la consola ni reintentar en bucle: si
    // no se sabe, no se pinta, que es el mismo resultado que «todo ok».
    retry: false,
  });

  const degraded = degradedServices(data);
  if (!isSystemAdmin || degraded.length === 0) return null;

  return (
    <Link
      href="/admin/dashboard"
      data-testid="system-health-indicator"
      title={t("healthDegradedTitle")}
      className={cn(
        "bg-warning-soft text-warning-soft-foreground",
        "hidden items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium sm:inline-flex",
      )}
    >
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{t("healthDegraded", { count: String(degraded.length) })}</span>
    </Link>
  );
}
