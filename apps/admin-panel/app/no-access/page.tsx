"use client";

/**
 * "Sin permisos" screen (ADR 0047, task_sso_03).
 *
 * Shown after a successful login (password OR SSO) when the user has ZERO
 * active tenant memberships. Their session is valid — it proves identity —
 * but access to any tenant is granted EXCLUSIVELY by a membership the
 * administrator assigns (no claiming, deny-by-default). So we tell them to
 * contact the admin and offer a clean logout; there is nothing else they
 * can do until an admin assigns them a tenant in `/admin/users`.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { clearToken, getToken } from "@/lib/auth";
import { setTenantId } from "@/lib/tenant-storage";

export default function NoAccessPage() {
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);

  // A direct hit with no token is meaningless — bounce to login.
  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
    }
  }, [router]);

  async function onLogout() {
    setLoggingOut(true);
    try {
      await apiFetch<void>("/auth/logout", { method: "POST" });
    } catch (err) {
      if (!(err instanceof ApiError)) console.error(err);
    } finally {
      clearToken();
      setTenantId(null);
      router.replace("/login");
    }
  }

  return (
    <main
      className="flex min-h-screen flex-col items-center justify-center gap-6 px-4 py-12"
      data-testid="no-access-screen"
    >
      <Card className="w-full max-w-md">
        <CardHeader className="flex flex-col items-center gap-3 text-center">
          <span
            className="bg-warning-soft text-warning-soft-foreground inline-flex h-14 w-14 items-center justify-center rounded-2xl"
            aria-hidden="true"
          >
            <ShieldAlert className="h-7 w-7" />
          </span>
          <CardTitle>Sin acceso a la plataforma</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="text-muted-foreground text-center text-sm">
            No tienes permisos asignados en la plataforma. Contacta con el administrador para que te
            asigne acceso a un espacio de trabajo.
          </p>
          <Button
            variant="outline"
            className="w-full"
            onClick={onLogout}
            disabled={loggingOut}
            data-testid="no-access-logout"
          >
            {loggingOut ? "Cerrando sesión…" : "Cerrar sesión"}
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
