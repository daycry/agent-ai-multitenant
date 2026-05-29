"use client";

/**
 * Tenant context for the admin shell.
 *
 * Lives just inside the auth gate in `app/admin/layout.tsx`. On
 * mount, fetches `/auth/me` to learn whether the user is a system
 * admin (the only role that actually exposes the picker). The
 * selected tenant id is mirrored to localStorage via
 * `lib/tenant-storage` so `apiFetch` can inject the
 * `X-Tenant-Id` header from any call site.
 *
 * Non-admins see no picker and the context just exposes
 * `isSuperadmin=false`.
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { getTenantId, setTenantId as persistTenantId } from "@/lib/tenant-storage";

interface MeResponse {
  id: string;
  email: string;
  full_name: string | null;
  is_system_admin: boolean;
  is_active: boolean;
}

interface TenantSummary {
  id: string;
  name: string;
  slug: string;
}

interface TenantContextValue {
  me: MeResponse | null;
  isSuperadmin: boolean;
  /** UUID of the currently active tenant, or null for "All tenants". */
  tenantId: string | null;
  /** Set null to clear (= portfolio view, no X-Tenant-Id header). */
  setTenantId: (id: string | null) => void;
  tenants: TenantSummary[];
  tenantsLoading: boolean;
  /** Re-fetch /admin/tenants — call after creating one. */
  refreshTenants: () => void;
}

const TenantContext = createContext<TenantContextValue | null>(null);

export function TenantProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [tenantId, setTenantIdState] = useState<string | null>(null);

  // Hydrate the in-memory tenant id from localStorage after mount
  // (avoids SSR/CSR mismatch — first render uses null).
  useEffect(() => {
    setTenantIdState(getTenantId());
  }, []);

  const meQuery = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiFetch<MeResponse>("/auth/me"),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const isSuperadmin = meQuery.data?.is_system_admin ?? false;

  // Tenants list only matters for the picker, so only superadmins
  // pay the cost of /admin/tenants.
  const tenantsQuery = useQuery({
    queryKey: ["admin", "tenants"],
    queryFn: () => apiFetch<TenantSummary[]>("/admin/tenants"),
    refetchOnWindowFocus: false,
    enabled: isSuperadmin,
  });

  const setTenantId = useCallback(
    (id: string | null) => {
      setTenantIdState(id);
      persistTenantId(id);
      // Drop tenant-scoped queries so the next read goes out with the new
      // X-Tenant-Id header — but NOT the queries that don't depend on the
      // tenant (frontend-admin-panel-2). `auth` is the user identity,
      // `admin`/`system-health` are platform-wide (BYPASSRLS) reads;
      // wiping them on every tenant switch forces needless refetches.
      const TENANT_INDEPENDENT_KEYS = new Set(["auth", "admin", "system-health"]);
      queryClient.invalidateQueries({
        predicate: (query) =>
          !query.queryKey.some(
            (key) => typeof key === "string" && TENANT_INDEPENDENT_KEYS.has(key),
          ),
      });
    },
    [queryClient],
  );

  const refreshTenants = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["admin", "tenants"] });
  }, [queryClient]);

  const value: TenantContextValue = {
    me: meQuery.data ?? null,
    isSuperadmin,
    tenantId,
    setTenantId,
    tenants: tenantsQuery.data ?? [],
    tenantsLoading: tenantsQuery.isLoading,
    refreshTenants,
  };

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

export function useTenantContext(): TenantContextValue {
  const ctx = useContext(TenantContext);
  if (!ctx) {
    throw new Error("useTenantContext must be used inside <TenantProvider>");
  }
  return ctx;
}
