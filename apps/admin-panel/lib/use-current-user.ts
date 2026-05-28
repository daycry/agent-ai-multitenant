"use client";

/**
 * Hook que consume `GET /me` y expone el rol del usuario en el tenant
 * activo (Plan 06.8 task_06_8_05).
 *
 * El backend devuelve `{user_id, email, full_name, is_system_admin,
 * memberships: [{tenant_id, tenant_name, role, is_active}],
 * active_tenant_id}`. El hook calcula los predicados que la UI usa
 * para mostrar / ocultar / deshabilitar botones.
 *
 * Reglas:
 *   - `isSystemAdmin`        — flag global `is_system_admin`.
 *   - `isTenantAdmin`        — rol `tenant_admin` en el tenant activo
 *                              (o `system_admin`, que siempre lo es).
 *   - `isTenantMember`       — membership activa en el tenant activo
 *                              (o `system_admin`).
 *   - `roleInActiveTenant`   — `tenant_admin | tenant_user | null`.
 *
 * Nota sobre `active_tenant_id`: cuando el superadmin selecciona un
 * tenant en el picker, el backend recibe `X-Tenant-Id` y `/me` lo
 * devuelve como `active_tenant_id`. Si no hay tenant activo (fresh
 * login, o superadmin sin selección) `roleInActiveTenant` es `null`
 * y `isTenantAdmin` queda `false` salvo system_admin.
 *
 * Cache: TanStack Query con `staleTime` de 5 min. Invalidar
 * manualmente al cambiar de tenant (lo hace `tenant-context.tsx` al
 * setTenantId).
 */

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export type UserRole = "tenant_admin" | "tenant_user" | "system_operator";

export interface MembershipSummary {
  tenant_id: string;
  tenant_name: string;
  role: UserRole;
  is_active: boolean;
}

export interface CurrentUser {
  user_id: string;
  email: string | null;
  full_name: string | null;
  is_system_admin: boolean;
  memberships: MembershipSummary[];
  active_tenant_id: string | null;
}

export interface UseCurrentUserResult {
  user: CurrentUser | null;
  isLoading: boolean;
  isError: boolean;
  isSystemAdmin: boolean;
  isTenantAdmin: boolean;
  isTenantMember: boolean;
  roleInActiveTenant: UserRole | null;
}

const FIVE_MINUTES_MS = 5 * 60 * 1000;

export function useCurrentUser(): UseCurrentUserResult {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<CurrentUser>("/me"),
    staleTime: FIVE_MINUTES_MS,
    refetchOnWindowFocus: false,
    retry: false,
  });

  const user = data ?? null;
  const isSystemAdmin = user?.is_system_admin ?? false;

  // El tenant activo manda; si no hay tenant_id en /me (fresh login o
  // superadmin sin picker) consideramos que no hay rol "en este
  // tenant" — system_admin sigue pasando todo igual.
  const activeMembership = user?.memberships.find(
    (m) => m.tenant_id === user.active_tenant_id && m.is_active,
  );
  const roleInActiveTenant: UserRole | null = activeMembership?.role ?? null;

  const isTenantAdmin = isSystemAdmin || roleInActiveTenant === "tenant_admin";
  const isTenantMember = isSystemAdmin || activeMembership !== undefined;

  return {
    user,
    isLoading,
    isError,
    isSystemAdmin,
    isTenantAdmin,
    isTenantMember,
    roleInActiveTenant,
  };
}
