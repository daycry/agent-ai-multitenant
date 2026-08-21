"use client";

import { QueryErrorResetBoundary } from "@tanstack/react-query";
import { type ReactNode } from "react";

import { AdminErrorBoundary } from "@/components/layout/error-boundary";
import { AdminShell } from "@/components/layout/admin-shell";
import { TenantProvider } from "@/lib/tenant-context";

/**
 * Persistent admin shell (sidebar + topbar).
 *
 * The auth gate that used to live here as a `useEffect` is GONE: it ran after
 * the protected page had been rendered and hydrated (a flash of protected UI),
 * and it could not be moved to the edge while the credential sat in
 * `localStorage`. With the session in an httpOnly cookie, `middleware.ts`
 * redirects before the page is generated (ADR 0133, task_prod09_08).
 */
export default function AdminLayout({ children }: { children: ReactNode }) {
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
