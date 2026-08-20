"use client";

/**
 * task_08_03 — UI de configuración OIDC (SSO) por tenant.
 *
 * Un `tenant_admin` configura aquí el inicio de sesión único OIDC del
 * tenant: elige una plantilla de proveedor (Azure AD, Google, Okta,
 * Auth0, GitHub, GitLab, Apple, Facebook), rellena client_id + secreto y
 * los campos específicos del IdP (issuer, parámetros como el tenant de
 * Azure o el dominio de Okta), activa/desactiva el flag `enabled`, y ve
 * la URL de callback/redirect que debe registrar en el IdP.
 *
 * Se AÑADE junto al login local (email+contraseña): activar SSO no
 * rompe el login local. El backend guarda el secreto cifrado en reposo
 * (o referenciado en Vault) y NUNCA lo devuelve — la UI solo sabe si hay
 * secreto configurado (`has_client_secret` + `client_secret_source`).
 *
 * Por la restricción única (tenant_id, provider) hay como mucho UNA
 * config OIDC por tenant, así que la pantalla gestiona una sola entrada:
 * crear / editar / activar / borrar.
 *
 * Endpoints backend (routers/sso.py, RBAC tenant_admin + RLS):
 *   GET    /auth/sso/config            — lista (0 o 1) — sin el secreto
 *   POST   /auth/sso/config            — crear
 *   PUT    /auth/sso/config/{id}       — editar (secreto opcional)
 *   DELETE /auth/sso/config/{id}       — borrar (soft delete)
 *   GET    /auth/sso/oidc/templates    — plantillas por IdP
 *   GET    /auth/sso/oidc/callback-url — URL a registrar en el IdP
 *
 * Permisos: LEER cualquier miembro del tenant; ESCRIBIR solo
 * `tenant_admin` (el backend devuelve 403). Envolvemos las acciones de
 * mutación en <RoleGuard min="tenant_admin">.
 */

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Shield } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { CallbackUrlCard } from "./callback-url-section";
import { SsoConfigCard } from "./sso-config-section";
import { SsoConfigDialog } from "./sso-config-dialog";
import type { CallbackUrl, SsoConfig, UpsertBody } from "./sso-types";

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function SsoConfigPage() {
  const errorText = useErrorText();
  const t = useT("ssoOidc");
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: ["sso-config"],
    queryFn: () => apiFetch<SsoConfig[]>("/auth/sso/config"),
    refetchOnWindowFocus: false,
  });

  const callbackQuery = useQuery({
    queryKey: ["sso-callback-url"],
    queryFn: () => apiFetch<CallbackUrl>("/auth/sso/oidc/callback-url"),
    refetchOnWindowFocus: false,
  });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SsoConfig | null>(null);

  // At most one OIDC config (unique constraint tenant_id+provider).
  const config = configQuery.data?.[0] ?? null;

  const saveMutation = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: UpsertBody }) =>
      id === null
        ? apiFetch<SsoConfig>("/auth/sso/config", { method: "POST", body })
        : apiFetch<SsoConfig>(`/auth/sso/config/${id}`, { method: "PUT", body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sso-config"] });
      setDialogOpen(false);
      setEditing(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/auth/sso/config/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sso-config"] });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ cfg, enabled }: { cfg: SsoConfig; enabled: boolean }) =>
      apiFetch<SsoConfig>(`/auth/sso/config/${cfg.id}`, {
        method: "PUT",
        body: {
          display_name: cfg.display_name,
          enabled,
          issuer: cfg.issuer,
          client_id: cfg.client_id,
          scopes: cfg.scopes,
          claim_mappings: cfg.claim_mappings,
        } satisfies UpsertBody,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sso-config"] });
    },
  });

  function handleCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function handleEdit() {
    setEditing(config);
    setDialogOpen(true);
  }

  function handleDelete() {
    if (config === null) return;
    if (!window.confirm(t("confirmDelete"))) return;
    deleteMutation.mutate(config.id);
  }

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="sso-config-page"
    >
      <PageHeader
        icon={<Shield className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="sso-config-header"
        actions={
          config === null ? (
            <RoleGuard min="tenant_admin">
              <Button onClick={handleCreate} data-testid="sso-create-button">
                <Plus className="mr-1 h-3.5 w-3.5" />
                {t("configure")}
              </Button>
            </RoleGuard>
          ) : null
        }
      />

      <p className="text-muted-foreground mt-2 text-sm" data-testid="sso-saml-link">
        {t("samlLinkQuestion")}{" "}
        <Link href="/admin/settings/sso/saml" className="text-primary underline">
          {t("samlLinkText")}
        </Link>
        .
      </p>

      <CallbackUrlCard
        url={callbackQuery.data?.callback_url ?? null}
        loading={callbackQuery.isLoading}
      />

      {configQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="sso-loading">
          {t("loading")}
        </p>
      ) : configQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="sso-load-error">
          {errorText(configQuery.error)}
        </p>
      ) : config === null ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="sso-empty">
              {t("emptyBefore")} <strong>“{t("configure")}”</strong> {t("emptyAfter")}
            </p>
          </CardContent>
        </Card>
      ) : (
        <SsoConfigCard
          config={config}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onToggle={(enabled) => toggleMutation.mutate({ cfg: config, enabled })}
          busy={deleteMutation.isPending || toggleMutation.isPending}
        />
      )}

      {deleteMutation.isError ? (
        <p className="text-destructive mt-3 text-xs" data-testid="sso-delete-error">
          {errorText(deleteMutation.error)}
        </p>
      ) : null}

      {dialogOpen ? (
        <SsoConfigDialog
          open={dialogOpen}
          onOpenChange={(next) => {
            if (!saveMutation.isPending) setDialogOpen(next);
          }}
          initial={editing}
          submitting={saveMutation.isPending}
          onSubmit={(body) => saveMutation.mutate({ id: editing?.id ?? null, body })}
          backendError={
            saveMutation.isError && saveMutation.error instanceof ApiError
              ? saveMutation.error.body
              : null
          }
        />
      ) : null}
    </div>
  );
}
