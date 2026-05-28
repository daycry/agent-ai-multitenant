"use client";

/**
 * Top bar visible en todas las pantallas `/admin/*`.
 *
 * - Móvil: hamburguesa + brand mark a la izquierda (el sidebar es un
 *   drawer aquí).
 * - Desktop: vacío a la izquierda (el sidebar fijo ya muestra la
 *   marca) y todo el contenido relevante a la derecha.
 * - Right cluster: selector de idioma (ES/EN) + menú de usuario con
 *   logout.
 *
 * El selector ES/EN consume `useLang()` del contexto montado en
 * `app/admin/layout.tsx`; cualquier pantalla que llame al hook ve
 * el cambio inmediatamente.
 */

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronDown, LogOut, Menu, Sparkles } from "lucide-react";

import { TenantPicker } from "@/components/layout/tenant-picker";
import { useLang, type Lang } from "@/lib/lang-context";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";
import { clearToken } from "@/lib/auth";
import { setTenantId as clearStoredTenant } from "@/lib/tenant-storage";
import { useCurrentUser } from "@/lib/use-current-user";

export function AdminHeader({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const { lang, setLang } = useLang();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);

  async function onLogout() {
    try {
      await apiFetch<void>("/auth/logout", { method: "POST" });
    } catch (err) {
      if (!(err instanceof ApiError)) console.error(err);
    } finally {
      clearToken();
      // Drop the active-tenant choice too — next login starts fresh.
      clearStoredTenant(null);
      router.replace("/login");
    }
  }

  return (
    <header
      className={cn(
        "bg-sidebar text-sidebar-foreground border-sidebar-border",
        "sticky top-0 z-30 flex h-20 items-center justify-between",
        "border-b px-6",
      )}
      data-testid="admin-header"
    >
      {/* Left: hamburguesa + brand sólo en móvil */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onOpenMobileNav}
          className={cn(
            "hover:bg-sidebar-border -ml-2 inline-flex h-10 w-10 items-center justify-center rounded-md",
            "md:hidden",
          )}
          aria-label="Abrir menú"
          data-testid="open-mobile-nav"
        >
          <Menu className="h-5 w-5" />
        </button>
        <Link
          href="/admin/dashboard"
          className="text-sidebar-foreground flex items-center gap-2 font-semibold tracking-tight md:hidden"
        >
          <span
            className={cn(
              "bg-brand-gradient inline-flex h-7 w-7 items-center justify-center rounded-md",
              "shadow-[0_0_24px_-4px_hsl(var(--gradient-from)/0.65)]",
            )}
          >
            <Sparkles className="h-4 w-4 text-white" />
          </span>
          <span>Agentic Platform</span>
        </Link>
      </div>

      {/* Right: tenant picker (superadmin only) + lang switcher + user menu */}
      <div className="flex items-center gap-3">
        <TenantPicker />
        <RoleBadge />
        <LangSwitcher lang={lang} onChange={setLang} />
        <UserMenu open={menuOpen} setOpen={setMenuOpen} onLogout={onLogout} />
      </div>
    </header>
  );
}

/**
 * Badge "system_admin | admin | user" junto al menú de usuario
 * (Plan 06.8 task_06_8_07). El color es ámbar para system_admin,
 * azul para admin y gris para user — codifica el nivel de poder
 * visualmente sin necesidad de leer el texto.
 */
function RoleBadge() {
  const { user, isSystemAdmin, roleInActiveTenant } = useCurrentUser();
  if (!user) return null;

  let label: string;
  let className: string;
  if (isSystemAdmin) {
    label = "system_admin";
    className = "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  } else if (roleInActiveTenant === "tenant_admin") {
    label = "admin";
    className = "bg-sky-500/15 text-sky-700 dark:text-sky-300";
  } else if (roleInActiveTenant === "tenant_user") {
    label = "user";
    className = "bg-muted text-muted-foreground";
  } else {
    // Logged in pero sin tenant activo — no badge.
    return null;
  }

  return (
    <span
      data-testid="role-badge"
      title={user.email ?? undefined}
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5",
        "text-xs font-semibold uppercase tracking-wide",
        className,
      )}
    >
      {label}
    </span>
  );
}

function LangSwitcher({ lang, onChange }: { lang: Lang; onChange: (l: Lang) => void }) {
  return (
    <div
      role="group"
      aria-label="Idioma"
      className={cn(
        "bg-sidebar-border/60 border-sidebar-border",
        "inline-flex items-center rounded-md border p-0.5 text-xs",
      )}
      data-testid="lang-switcher"
    >
      {(["es", "en"] as const).map((value) => {
        const active = lang === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => onChange(value)}
            aria-pressed={active}
            data-testid={`lang-${value}`}
            className={cn(
              "rounded px-2.5 py-1 font-semibold uppercase tracking-wide transition-colors",
              active
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-sidebar-muted-foreground hover:text-sidebar-foreground",
            )}
          >
            {value}
          </button>
        );
      })}
    </div>
  );
}

function UserMenu({
  open,
  setOpen,
  onLogout,
}: {
  open: boolean;
  setOpen: (o: boolean) => void;
  onLogout: () => void;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          "hover:bg-sidebar-border text-sidebar-foreground",
          "inline-flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
        )}
        data-testid="user-menu"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="bg-brand-gradient flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold text-white">
          A
        </span>
        <ChevronDown className="text-sidebar-muted-foreground h-3 w-3" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            className="bg-popover absolute right-0 top-full z-50 mt-1 w-44 rounded-md border shadow-lg"
            role="menu"
            data-testid="user-menu-popover"
          >
            <button
              type="button"
              onClick={onLogout}
              data-testid="logout"
              className="hover:bg-muted flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm"
              role="menuitem"
            >
              <LogOut className="h-4 w-4" />
              Cerrar sesión
            </button>
          </div>
        </>
      )}
    </div>
  );
}
