"use client";

/**
 * task_08_06 — UI de configuración SAML 2.0 (SSO) por tenant.
 *
 * Un `tenant_admin` configura aquí el inicio de sesión único SAML del
 * tenant: pega/sube los metadatos XML del IdP (el backend extrae
 * entityId, URL de SSO y certificado), ajusta el formato de NameID y el
 * mapeo de atributos, activa/desactiva el flag `enabled`, y ve los
 * metadatos del SP (EntityID + ACS URL) que debe registrar en el IdP.
 *
 * Se AÑADE junto al login local (email+contraseña) y junto al SSO OIDC
 * de la Fase A: activar SAML no rompe ninguno de los otros. El backend
 * guarda la clave privada del SP cifrada en reposo (o referenciada en
 * Vault) y NUNCA la devuelve — la UI solo sabe si hay clave configurada
 * (`has_sp_private_key` + `sp_private_key_source`). El certificado del
 * IdP y el certificado público del SP no son secretos y sí circulan.
 *
 * Por la restricción única (tenant_id, provider) hay como mucho UNA
 * config SAML por tenant: crear / editar / activar / borrar.
 *
 * Endpoints backend (routers/sso.py, RBAC tenant_admin + RLS):
 *   GET    /auth/sso/saml/config              — lista (0 o 1) — sin la clave
 *   POST   /auth/sso/saml/config              — crear
 *   PUT    /auth/sso/saml/config/{id}         — editar (clave opcional)
 *   DELETE /auth/sso/saml/config/{id}         — borrar (soft delete)
 *   GET    /auth/sso/saml/sp-metadata         — EntityID + ACS del SP
 *   POST   /auth/sso/saml/parse-metadata      — parsear metadatos del IdP
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

import { SamlConfigCard } from "./saml-config-section";
import { SamlConfigDialog } from "./saml-config-dialog";
import type { SamlConfig, SpMetadata, UpsertBody } from "./saml-types";
import { SpMetadataCard } from "./sp-metadata-section";
// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function SamlConfigPage() {
  const t = useT("ssoSaml");
  const queryClient = useQueryClient();

  const configQuery = useQuery({
    queryKey: ["saml-config"],
    queryFn: () => apiFetch<SamlConfig[]>("/auth/sso/saml/config"),
    refetchOnWindowFocus: false,
  });

  const metadataQuery = useQuery({
    queryKey: ["saml-sp-metadata"],
    queryFn: () => apiFetch<SpMetadata>("/auth/sso/saml/sp-metadata"),
    refetchOnWindowFocus: false,
  });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<SamlConfig | null>(null);

  // At most one SAML config (unique constraint tenant_id+provider).
  const config = configQuery.data?.[0] ?? null;

  const saveMutation = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: UpsertBody }) =>
      id === null
        ? apiFetch<SamlConfig>("/auth/sso/saml/config", { method: "POST", body })
        : apiFetch<SamlConfig>(`/auth/sso/saml/config/${id}`, { method: "PUT", body }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saml-config"] });
      setDialogOpen(false);
      setEditing(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/auth/sso/saml/config/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saml-config"] });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ cfg, enabled }: { cfg: SamlConfig; enabled: boolean }) =>
      apiFetch<SamlConfig>(`/auth/sso/saml/config/${cfg.id}`, {
        method: "PUT",
        body: {
          display_name: cfg.display_name,
          enabled,
          idp_entity_id: cfg.idp_entity_id,
          idp_sso_url: cfg.idp_sso_url,
          idp_x509_cert: cfg.idp_x509_cert,
          name_id_format: cfg.name_id_format,
          attribute_mappings: cfg.attribute_mappings,
          sp_x509_cert: cfg.sp_x509_cert,
          authn_requests_signed: cfg.authn_requests_signed,
          want_assertions_signed: cfg.want_assertions_signed,
          want_assertions_encrypted: cfg.want_assertions_encrypted,
          want_name_id_encrypted: cfg.want_name_id_encrypted,
        } satisfies UpsertBody,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["saml-config"] });
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
      data-testid="saml-config-page"
    >
      <PageHeader
        icon={<Shield className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="saml-config-header"
        actions={
          config === null ? (
            <RoleGuard min="tenant_admin">
              <Button onClick={handleCreate} data-testid="saml-create-button">
                <Plus className="mr-1 h-3.5 w-3.5" />
                {t("configure")}
              </Button>
            </RoleGuard>
          ) : null
        }
      />

      <p className="text-muted-foreground mt-2 text-sm" data-testid="saml-oidc-link">
        {t("oidcLinkQuestion")}{" "}
        <Link href="/admin/settings/sso" className="text-primary underline">
          {t("oidcLinkText")}
        </Link>
        .
      </p>

      <SpMetadataCard metadata={metadataQuery.data ?? null} loading={metadataQuery.isLoading} />

      {configQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm" data-testid="saml-loading">
          {t("loading")}
        </p>
      ) : configQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="saml-load-error">
          {configQuery.error instanceof ApiError
            ? configQuery.error.body
            : String(configQuery.error)}
        </p>
      ) : config === null ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="saml-empty">
              {t("emptyBefore")} <strong>“{t("configure")}”</strong> {t("emptyAfter")}
            </p>
          </CardContent>
        </Card>
      ) : (
        <SamlConfigCard
          config={config}
          onEdit={handleEdit}
          onDelete={handleDelete}
          onToggle={(enabled) => toggleMutation.mutate({ cfg: config, enabled })}
          busy={deleteMutation.isPending || toggleMutation.isPending}
        />
      )}

      {deleteMutation.isError ? (
        <p className="text-destructive mt-3 text-xs" data-testid="saml-delete-error">
          {deleteMutation.error instanceof ApiError
            ? deleteMutation.error.body
            : String(deleteMutation.error)}
        </p>
      ) : null}

      {dialogOpen ? (
        <SamlConfigDialog
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
