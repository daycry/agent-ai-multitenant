"use client";

/**
 * Tenant-picker for the `multiple` post-login state (ADR 0047, task_sso_03).
 *
 * Reached after login when the user has MORE THAN ONE active membership.
 * It lists those tenants and lets the user pick one; the choice POSTs
 * `/auth/session/select-tenant`, which mints a TENANT-SCOPED token (the
 * backend re-asserts the membership) before entering the app. This is the
 * regular-user counterpart of the superadmin header picker — both end up
 * setting the active tenant; this one also re-mints the token because a
 * regular user cannot use the `X-Tenant-Id` superadmin override.
 *
 * It lives outside the `/admin` shell (no tenant context yet, so the
 * sidebar/topbar must not render) and re-resolves on mount so a deep-link
 * with the wrong state lands correctly (no membership → no-access, one →
 * straight in).
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Building2 } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { getToken } from "@/lib/auth";
import {
  HOME_ROUTE,
  NO_ACCESS_ROUTE,
  resolveSession,
  selectTenant,
  setTokenForSingle,
  type ResolvedMembership,
} from "@/lib/session";

export default function SelectTenantPage() {
  const router = useRouter();
  const [memberships, setMemberships] = useState<ResolvedMembership[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const resolution = await resolveSession();
        if (cancelled) return;
        if (resolution.state === "no_access") {
          router.replace(NO_ACCESS_ROUTE);
          return;
        }
        if (resolution.state === "single") {
          // Only one tenant — enter it directly (the backend already
          // minted the tenant-scoped token in the resolution).
          setTokenForSingle(resolution);
          router.replace(HOME_ROUTE);
          return;
        }
        setMemberships(resolution.memberships);
      } catch {
        if (!cancelled) setError("No se pudo cargar la lista de espacios de trabajo.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function onPick(tenantId: string) {
    setPending(tenantId);
    setError(null);
    try {
      await selectTenant(tenantId);
      router.push(HOME_ROUTE);
    } catch {
      setError("No se pudo activar ese espacio de trabajo. Inténtalo de nuevo.");
      setPending(null);
    }
  }

  return (
    <main
      className="flex min-h-screen flex-col items-center justify-center gap-6 px-4 py-12"
      data-testid="select-tenant-screen"
    >
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Elige un espacio de trabajo</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-muted-foreground text-sm">
            Tienes acceso a varios espacios. Selecciona con cuál quieres entrar.
          </p>
          {memberships === null && !error && (
            <div className="flex items-center justify-center py-6">
              <Spinner className="h-5 w-5" />
            </div>
          )}
          {memberships?.map((m) => (
            <button
              key={m.tenant_id}
              type="button"
              onClick={() => onPick(m.tenant_id)}
              disabled={pending !== null}
              data-testid={`select-tenant-option-${m.tenant_id}`}
              className="hover:bg-muted flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2.5 text-left text-sm transition-colors disabled:opacity-60"
            >
              <span className="flex items-center gap-2 truncate">
                <Building2 className="h-4 w-4 shrink-0" />
                <span className="truncate font-medium">{m.tenant_name}</span>
              </span>
              {pending === m.tenant_id ? (
                <Spinner className="h-4 w-4" />
              ) : (
                <span className="text-muted-foreground text-xs">{m.role}</span>
              )}
            </button>
          ))}
          {error && (
            <p className="text-destructive text-sm" role="alert" data-testid="select-tenant-error">
              {error}
            </p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
