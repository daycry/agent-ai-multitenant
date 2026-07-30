"use client";

import { QueryErrorResetBoundary } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { AdminErrorBoundary } from "@/components/layout/error-boundary";
import { AdminShell } from "@/components/layout/admin-shell";
import { useT } from "@/lib/i18n";
import { TenantProvider } from "@/lib/tenant-context";
import { getToken } from "@/lib/auth";

/**
 * Client-side auth gate + persistent admin shell (sidebar + topbar).
 * Phase 0 only — phase 15 will move the gate to a middleware-level
 * redirect once we use httpOnly cookies that the Next.js edge can
 * read at request time.
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const t = useT("common");

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) {
    return (
      <main className="bg-background flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground text-sm">{t("loading")}</p>
      </main>
    );
  }

  // `LanguageProvider` ya lo monta `app/providers.tsx` (layout raíz) desde
  // prod-16 `task_prod16_01`; montarlo otra vez aquí desconectaría el selector
  // del header de las pantallas de sesión.
  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <AdminErrorBoundary onReset={reset}>
          <TenantProvider>
            <AdminShell>{children}</AdminShell>
          </TenantProvider>
        </AdminErrorBoundary>
      )}
    </QueryErrorResetBoundary>
  );
}
