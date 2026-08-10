"use client";

/**
 * Ficha de la (única) configuración SAML del tenant: IdP, NameID, de dónde sale
 * la clave del SP y las acciones de tenant_admin.
 */

import { KeyRound, Pencil, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";

import { KEY_SOURCE_LABEL, type SamlConfig } from "./saml-types";

// --------------------------------------------------------------------------
// Config card — the single SAML config
// --------------------------------------------------------------------------
export function SamlConfigCard({
  config,
  onEdit,
  onDelete,
  onToggle,
  busy,
}: {
  config: SamlConfig;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
  busy: boolean;
}) {
  return (
    <Card className="mt-6" data-testid="saml-config-card">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span className="truncate">{config.display_name || "SAML"}</span>
            <Badge variant={config.enabled ? "success" : "muted"} data-testid="saml-enabled-badge">
              {config.enabled ? "activo" : "inactivo"}
            </Badge>
            {config.has_sp_private_key ? (
              <Badge variant="info" data-testid="saml-key-badge">
                <KeyRound className="mr-1 h-3 w-3" />
                clave SP: {KEY_SOURCE_LABEL[config.sp_private_key_source ?? "encrypted"]}
              </Badge>
            ) : (
              <Badge variant="muted" data-testid="saml-no-key-badge">
                sin clave SP
              </Badge>
            )}
            {config.authn_requests_signed ? (
              <Badge variant="info" data-testid="saml-signed-badge">
                AuthnRequest firmado
              </Badge>
            ) : null}
          </CardTitle>
          <dl className="text-muted-foreground mt-2 space-y-1 text-xs">
            <div className="flex gap-2">
              <dt className="font-medium">IdP Entity ID:</dt>
              <dd className="break-all font-mono" data-testid="saml-config-entity-id">
                {config.idp_entity_id}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">SSO URL:</dt>
              <dd className="break-all font-mono" data-testid="saml-config-sso-url">
                {config.idp_sso_url}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">NameID:</dt>
              <dd className="break-all font-mono" data-testid="saml-config-name-id">
                {config.name_id_format}
              </dd>
            </div>
          </dl>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <RoleGuard min="tenant_admin">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onToggle(!config.enabled)}
              disabled={busy}
              data-testid="saml-toggle-enabled"
            >
              {config.enabled ? "Desactivar" : "Activar"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onEdit}
              disabled={busy}
              data-testid="saml-edit-button"
              aria-label="Editar"
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onDelete}
              disabled={busy}
              data-testid="saml-delete-button"
              aria-label="Eliminar"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </RoleGuard>
        </div>
      </CardHeader>
    </Card>
  );
}
