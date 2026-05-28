"use client";

/**
 * `<RoleGuard>` — muestra / oculta children según el rol del usuario
 * en el tenant activo (Plan 06.8 task_06_8_06).
 *
 * Uso típico (envolver botones de mutación admin-only):
 *
 *   <RoleGuard min="tenant_admin">
 *     <Button onClick={createProject}>Crear proyecto</Button>
 *   </RoleGuard>
 *
 * Si no cumple el rol mínimo:
 *   - `fallback` se renderiza en su lugar (e.g. botón deshabilitado
 *     con tooltip). Si no se da, no se renderiza nada.
 *
 * `min` acepta:
 *   - `tenant_member`  — basta con membership activa.
 *   - `tenant_admin`   — requiere `tenant_admin` o `system_admin`.
 *   - `system_admin`   — requiere flag global.
 *
 * El backend valida igualmente (el gate está en `auth/deps.py`); este
 * componente sirve para que la UI no muestre acciones que sabemos
 * que el backend va a rechazar.
 */

import type { ReactNode } from "react";

import { useCurrentUser } from "@/lib/use-current-user";

export type RoleGuardLevel = "tenant_member" | "tenant_admin" | "system_admin";

interface RoleGuardProps {
  min: RoleGuardLevel;
  children: ReactNode;
  fallback?: ReactNode;
}

export function RoleGuard({ min, children, fallback = null }: RoleGuardProps) {
  const { isSystemAdmin, isTenantAdmin, isTenantMember, isLoading } = useCurrentUser();

  // Mientras carga, no parpadear renderizando ni el fallback ni el
  // children. La UI se ve un instante "vacía"; preferible a un flash
  // del botón admin a un user.
  if (isLoading) return null;

  const allowed =
    (min === "system_admin" && isSystemAdmin) ||
    (min === "tenant_admin" && isTenantAdmin) ||
    (min === "tenant_member" && isTenantMember);

  return <>{allowed ? children : fallback}</>;
}
