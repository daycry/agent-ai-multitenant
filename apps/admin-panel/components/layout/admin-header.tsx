"use client";

/**
 * Top bar visible en todas las pantallas `/admin/*`.
 *
 * - Móvil: hamburguesa + brand mark a la izquierda (el sidebar es un
 *   drawer aquí).
 * - Desktop: vacío a la izquierda (el sidebar fijo ya muestra la
 *   marca) y todo el contenido relevante a la derecha.
 * - Right cluster (Plan admin-menu-reorg task_menu_02):
 *     1. **Tenant actual** — un pill con el nombre del tenant activo.
 *        Para el System Admin es el `TenantPicker` (puede cambiar de
 *        tenant); para un tenant_admin/user es un pill estático cuyo
 *        nombre se resuelve desde las memberships de `useCurrentUser`.
 *     2. Selector de idioma (ES/EN).
 *     3. **Menú de usuario** — avatar con la inicial + nombre/email y
 *        un dropdown con Perfil / Cerrar sesión. El logout es el de
 *        siempre (POST /auth/logout → limpiar token + tenant → /login).
 *
 * El selector ES/EN consume `useLang()` del contexto montado en
 * `app/admin/layout.tsx`; cualquier pantalla que llame al hook ve
 * el cambio inmediatamente.
 *
 * Behavior-preserving: se conservan TODAS las rutas, llamadas a la
 * API y los `data-testid` existentes (admin-header, open-mobile-nav,
 * lang-switcher/lang-es/lang-en, role-badge, user-menu,
 * user-menu-popover, logout). El TenantPicker mantiene los suyos.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Building2, ChevronDown, LogOut, Menu, Sparkles, UserRound } from "lucide-react";

import { TenantPicker } from "@/components/layout/tenant-picker";
import { useLang, type Lang } from "@/lib/lang-context";
import { cn } from "@/lib/utils";
import { ApiError, apiFetch } from "@/lib/api";
import { clearToken } from "@/lib/auth";
import { setTenantId as clearStoredTenant } from "@/lib/tenant-storage";
import { useCurrentUser, type CurrentUser } from "@/lib/use-current-user";

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

      {/* Right: tenant actual (picker para superadmin) + lang + menú usuario */}
      <div className="flex items-center gap-2 sm:gap-3">
        <TenantArea />
        <span aria-hidden="true" className="bg-sidebar-border hidden h-6 w-px sm:block" />
        <RoleBadge />
        <LangSwitcher lang={lang} onChange={setLang} />
        <UserMenu open={menuOpen} setOpen={setMenuOpen} onLogout={onLogout} />
      </div>
    </header>
  );
}

/**
 * Zona del tenant actual.
 *
 * - System Admin: el `TenantPicker` (puede cambiar de tenant). Mantiene
 *   sus propios `data-testid` y muestra el nombre del tenant activo.
 * - tenant_admin / tenant_user: pill estático con el nombre del tenant
 *   activo resuelto desde las memberships (`/me`). No es interactivo
 *   porque estos roles no eligen tenant en esta versión.
 */
function TenantArea() {
  const { user, isSystemAdmin } = useCurrentUser();

  if (isSystemAdmin) {
    return <TenantPicker />;
  }

  const name = resolveActiveTenantName(user);
  if (!name) return null;

  return (
    <span
      data-testid="current-tenant"
      title={name}
      className={cn(
        "border-sidebar-border bg-sidebar-border/40 text-sidebar-foreground",
        "inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm",
      )}
    >
      <Building2 className="h-3.5 w-3.5 shrink-0 opacity-80" />
      <span className="max-w-[160px] truncate font-medium" data-testid="current-tenant-name">
        {name}
      </span>
    </span>
  );
}

/** Nombre del tenant activo a partir de las memberships de `/me`. */
function resolveActiveTenantName(user: CurrentUser | null): string | null {
  if (!user?.active_tenant_id) return null;
  const membership = user.memberships.find(
    (m) => m.tenant_id === user.active_tenant_id && m.is_active,
  );
  return membership?.tenant_name ?? null;
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

/** Inicial para el avatar: primera letra del nombre, si no del email. */
function avatarInitial(user: CurrentUser | null): string {
  const source = user?.full_name?.trim() || user?.email?.trim() || "";
  return source ? source.charAt(0).toUpperCase() : "?";
}

/** Texto principal mostrado junto al avatar (nombre o, en su defecto, email). */
function displayName(user: CurrentUser | null): string {
  return user?.full_name?.trim() || user?.email?.trim() || "Mi cuenta";
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
  const { user } = useCurrentUser();
  const menuRef = useRef<HTMLDivElement>(null);
  const firstItemRef = useRef<HTMLAnchorElement>(null);

  const initial = avatarInitial(user);
  const name = displayName(user);
  const email = user?.email ?? null;
  // Evita duplicar nombre/email cuando coinciden (login por email sin nombre).
  const showEmailLine = Boolean(email) && email !== name;

  // Cerrar con Escape y mover el foco al primer ítem al abrir.
  useEffect(() => {
    if (!open) return;
    firstItemRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  return (
    <div className="relative" ref={menuRef}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          "hover:bg-sidebar-border text-sidebar-foreground",
          "inline-flex items-center gap-2 rounded-md px-1.5 py-1 text-sm transition-colors sm:px-2 sm:py-1.5",
        )}
        data-testid="user-menu"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Cuenta de ${name}`}
        title={name}
      >
        <span
          aria-hidden="true"
          className="bg-brand-gradient flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white"
        >
          {initial}
        </span>
        <span className="hidden max-w-[160px] flex-col items-start leading-tight lg:flex">
          <span className="truncate font-medium">{name}</span>
          {showEmailLine && (
            <span className="text-sidebar-muted-foreground truncate text-xs font-normal">
              {email}
            </span>
          )}
        </span>
        <ChevronDown className="text-sidebar-muted-foreground hidden h-3 w-3 shrink-0 sm:block" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            className="bg-popover text-popover-foreground absolute right-0 top-full z-50 mt-1 w-56 overflow-hidden rounded-md border shadow-lg"
            role="menu"
            aria-label="Menú de usuario"
            data-testid="user-menu-popover"
          >
            {/* Identidad: nombre + email (también visible cuando el botón los oculta). */}
            <div className="border-b px-3 py-2.5">
              <p className="truncate text-sm font-medium">{name}</p>
              {showEmailLine && <p className="text-muted-foreground truncate text-xs">{email}</p>}
            </div>
            <div className="p-1">
              <Link
                ref={firstItemRef}
                href="/admin/settings"
                onClick={() => setOpen(false)}
                data-testid="user-menu-profile"
                className="hover:bg-muted focus-visible:bg-muted flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm outline-none"
                role="menuitem"
              >
                <UserRound className="h-4 w-4" />
                Perfil
              </Link>
              <button
                type="button"
                onClick={onLogout}
                data-testid="logout"
                className="hover:bg-muted focus-visible:bg-muted flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm outline-none"
                role="menuitem"
              >
                <LogOut className="h-4 w-4" />
                Cerrar sesión
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
