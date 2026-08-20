"use client";

/**
 * Tenant picker dropdown shown in the admin header for superadmins.
 *
 * - "Todos los tenants" (value === null) clears the active tenant
 *   so subsequent requests omit X-Tenant-Id and BYPASSRLS returns
 *   the portfolio view.
 * - Picking a specific tenant sets X-Tenant-Id on every subsequent
 *   apiFetch call.
 * - "+ Crear tenant" opens a dialog that POSTs /admin/tenants and
 *   auto-selects the new tenant — this is the only way to bootstrap
 *   the first tenant from the UI (a fresh superadmin starts with
 *   none).
 *
 * Hidden when the current user isn't a superadmin (regular users
 * don't have a choice of tenants in this version).
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Building2, Check, ChevronDown, Globe, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { useT } from "@/lib/i18n";
import { useTenantContext } from "@/lib/tenant-context";
import { useErrorText } from "@/lib/use-error-text";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";

// The platform tenant is reserved for built-in catalogs (CLAUDE.md
// §1). It must never appear as a selectable "acting tenant".
const PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000001";

interface TenantSummary {
  id: string;
  name: string;
  slug: string;
}

/** name -> slug: lowercase, strip accents, non-alphanumerics to hyphens. */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "") // strip combining diacritics
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function TenantPicker() {
  /*
   * El traductor NO puede llamarse `t` aquí: `t` es ya el nombre del tenant en
   * cuatro callbacks de este mismo cuerpo (`filter`, `find`, dos `map`), y una
   * llamada a `t("clave")` dentro de cualquiera de ellos invocaría al objeto
   * tenant. El compilador no lo caza —un objeto tampoco es invocable, pero el
   * error saldría en runtime— y ya pasó una vez en el wizard de proyectos
   * (prod-16 `task_prod16_03`).
   */
  const tPicker = useT("tenantPicker");
  const tCommon = useT("common");
  const {
    isSuperadmin,
    tenantId,
    setTenantId,
    tenants: allTenants,
    tenantsLoading,
    refreshTenants,
  } = useTenantContext();
  const [open, setOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  if (!isSuperadmin) return null;

  const tenants = allTenants.filter((t) => t.id !== PLATFORM_TENANT_ID);
  const current = tenants.find((t) => t.id === tenantId) ?? null;
  const label = current?.name ?? tPicker("allTenants");

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
                <span>{tPicker("allTenants")}</span>
                <span className="text-muted-foreground text-xs">{tPicker("portfolioHint")}</span>
              </span>
              {tenantId === null && <Check className="text-primary h-3.5 w-3.5" />}
            </button>
            <div className="my-1 border-t" />
            {tenantsLoading && (
              <p className="text-muted-foreground px-3 py-2 text-xs">{tCommon("loading")}</p>
            )}
            {!tenantsLoading && tenants.length === 0 && (
              <p
                className="text-muted-foreground px-3 py-2 text-xs"
                data-testid="tenant-picker-empty"
              >
                {tPicker("empty")}
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
            <div className="my-1 border-t" />
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                setCreateOpen(true);
              }}
              data-testid="tenant-picker-create"
              className={cn(
                "text-primary hover:bg-muted flex w-full items-center gap-2",
                "rounded-md px-3 py-2 text-left text-sm font-medium",
              )}
            >
              <Plus className="h-3.5 w-3.5" />
              {tPicker("create")}
            </button>
          </div>
        </>
      )}

      <CreateTenantDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        existingSlugs={allTenants.map((t) => t.slug)}
        onCreated={(created) => {
          refreshTenants();
          setTenantId(created.id);
        }}
      />
    </div>
  );
}

// --------------------------------------------------------------------------
// Create-tenant dialog
// --------------------------------------------------------------------------
function CreateTenantDialog({
  open,
  onOpenChange,
  existingSlugs,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  existingSlugs: string[];
  onCreated: (tenant: TenantSummary) => void;
}) {
  const t = useT("tenantPicker");
  const errorText = useErrorText();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  // Track whether the user hand-edited the slug; until then it
  // auto-follows the name.
  const [slugTouched, setSlugTouched] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setSlug("");
    setSlugTouched(false);
    setError(null);
  }

  const effectiveSlug = slugTouched ? slug : slugify(name);
  const slugValid = /^[a-z0-9][a-z0-9-]*$/.test(effectiveSlug);
  const slugTaken = existingSlugs.includes(effectiveSlug);

  const create = useMutation({
    mutationFn: () =>
      apiFetch<TenantSummary>("/admin/tenants", {
        method: "POST",
        body: { name: name.trim(), slug: effectiveSlug },
      }),
    onSuccess: (created) => {
      onCreated(created);
      onOpenChange(false);
      reset();
    },
    // prod-16 `task_prod16_05`: `err.body` pintaba el cuerpo crudo del backend
    // —el HTML de un gateway, el traceback de un 500— en la cabecera del panel.
    onError: (err: unknown) => {
      setError(errorText(err));
    },
  });

  const canSubmit = name.trim().length > 0 && slugValid && !slugTaken && !create.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent data-testid="create-tenant-dialog">
        <DialogHeader>
          <DialogTitle>{t("create")}</DialogTitle>
          <DialogDescription>{t("createDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tenant-name">{t("nameLabel")}</Label>
            <Input
              id="tenant-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
              placeholder={t("namePlaceholder")}
              data-testid="create-tenant-name"
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tenant-slug">{t("slugLabel")}</Label>
            <Input
              id="tenant-slug"
              value={effectiveSlug}
              onChange={(e) => {
                setSlugTouched(true);
                setSlug(e.target.value);
                setError(null);
              }}
              placeholder="equipo-plataforma"
              data-testid="create-tenant-slug"
            />
            <p className="text-muted-foreground text-xs">{t("slugHelp")}</p>
            {effectiveSlug.length > 0 && !slugValid && (
              <p className="text-danger-soft-foreground text-xs">{t("slugInvalid")}</p>
            )}
            {slugValid && slugTaken && (
              <p className="text-danger-soft-foreground text-xs">{t("slugTaken")}</p>
            )}
          </div>
          {error && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="create-tenant-error"
            >
              {error}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="create-tenant-cancel"
          >
            {t("cancel")}
          </Button>
          <Button
            onClick={() => create.mutate()}
            disabled={!canSubmit}
            data-testid="create-tenant-submit"
          >
            {create.isPending && <Spinner className="mr-2 h-4 w-4" />}
            {create.isPending ? t("creating") : t("create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
