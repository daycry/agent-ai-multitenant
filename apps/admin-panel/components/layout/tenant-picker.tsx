"use client";

/**
 * Tenant picker dropdown shown in the admin header for superadmins.
 *
 * - "Todos los tenants" (value === null) clears the active tenant
 *   so subsequent requests omit X-Tenant-Id and BYPASSRLS returns
 *   the portfolio view.
 * - Picking a specific tenant sets X-Tenant-Id on every subsequent
 *   apiFetch call.
 *
 * Hidden when the current user isn't a superadmin (regular users
 * don't have a choice of tenants in this version).
 */

import { useState } from "react";
import { Building2, Check, ChevronDown, Globe } from "lucide-react";

import { useTenantContext } from "@/lib/tenant-context";
import { cn } from "@/lib/utils";

// The platform tenant is reserved for built-in catalogs (CLAUDE.md
// §1). It must never appear as a selectable "acting tenant".
const PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001";

export function TenantPicker() {
  const {
    isSuperadmin,
    tenantId,
    setTenantId,
    tenants: allTenants,
    tenantsLoading,
  } = useTenantContext();
  const [open, setOpen] = useState(false);

  if (!isSuperadmin) return null;

  const tenants = allTenants.filter((t) => t.id !== PLATFORM_TENANT_ID);
  const current = tenants.find((t) => t.id === tenantId) ?? null;
  const label = current?.name ?? "Todos los tenants";

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          "text-sidebar-foreground hover:bg-sidebar-border",
          "inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors",
          "border-sidebar-border bg-sidebar-border/40",
        )}
        data-testid="tenant-picker"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {current ? <Building2 className="h-3.5 w-3.5" /> : <Globe className="h-3.5 w-3.5" />}
        <span className="max-w-[160px] truncate" data-testid="tenant-picker-label">
          {label}
        </span>
        <ChevronDown className="text-sidebar-muted-foreground h-3 w-3" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            className="bg-popover absolute right-0 top-full z-50 mt-1 w-64 rounded-md border shadow-lg"
            role="menu"
            data-testid="tenant-picker-popover"
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setTenantId(null);
                setOpen(false);
              }}
              data-testid="tenant-picker-all"
              className={cn(
                "hover:bg-muted flex w-full items-center justify-between gap-2",
                "rounded-md px-3 py-2 text-left text-sm",
              )}
            >
              <span className="flex items-center gap-2">
                <Globe className="h-3.5 w-3.5" />
                <span>Todos los tenants</span>
                <span className="text-muted-foreground text-xs">(portfolio)</span>
              </span>
              {tenantId === null && <Check className="text-primary h-3.5 w-3.5" />}
            </button>
            <div className="my-1 border-t" />
            {tenantsLoading && <p className="text-muted-foreground px-3 py-2 text-xs">Cargando…</p>}
            {!tenantsLoading && tenants.length === 0 && (
              <p
                className="text-muted-foreground px-3 py-2 text-xs"
                data-testid="tenant-picker-empty"
              >
                Aún no hay tenants. Crea uno desde la sección de administración.
              </p>
            )}
            {tenants.map((t) => {
              const active = t.id === tenantId;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setTenantId(t.id);
                    setOpen(false);
                  }}
                  data-testid={`tenant-picker-option-${t.id}`}
                  className={cn(
                    "hover:bg-muted flex w-full items-center justify-between gap-2",
                    "rounded-md px-3 py-2 text-left text-sm",
                  )}
                >
                  <span className="flex items-center gap-2 truncate">
                    <Building2 className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">{t.name}</span>
                  </span>
                  {active && <Check className="text-primary h-3.5 w-3.5" />}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
