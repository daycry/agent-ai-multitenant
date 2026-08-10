"use client";

/**
 * Ficha de la (única) configuración OIDC del tenant: issuer, client id, scopes,
 * de dónde sale el secreto, y las acciones de tenant_admin.
 */

import { KeyRound, Pencil, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";

import { SECRET_SOURCE_LABEL, type SsoConfig } from "./sso-types";

// --------------------------------------------------------------------------
// Config card — the single OIDC config
// --------------------------------------------------------------------------
export function SsoConfigCard({
  config,
  onEdit,
  onDelete,
  onToggle,
  busy,
}: {
  config: SsoConfig;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
  busy: boolean;
}) {
  return (
    <Card className="mt-6" data-testid="sso-config-card">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <span className="truncate">{config.display_name || "OIDC"}</span>
            <Badge variant={config.enabled ? "success" : "muted"} data-testid="sso-enabled-badge">
              {config.enabled ? "activo" : "inactivo"}
            </Badge>
            {config.has_client_secret ? (
              <Badge variant="info" data-testid="sso-secret-badge">
                <KeyRound className="mr-1 h-3 w-3" />
                secreto: {SECRET_SOURCE_LABEL[config.client_secret_source ?? "encrypted"]}
              </Badge>
            ) : (
              <Badge variant="warning" data-testid="sso-no-secret-badge">
                sin secreto
              </Badge>
            )}
          </CardTitle>
          <dl className="text-muted-foreground mt-2 space-y-1 text-xs">
            <div className="flex gap-2">
              <dt className="font-medium">Issuer:</dt>
              <dd className="break-all font-mono" data-testid="sso-config-issuer">
                {config.issuer}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Client ID:</dt>
              <dd className="break-all font-mono" data-testid="sso-config-client-id">
                {config.client_id}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="font-medium">Scopes:</dt>
              <dd className="font-mono" data-testid="sso-config-scopes">
                {config.scopes.join(" ")}
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
              data-testid="sso-toggle-enabled"
            >
              {config.enabled ? "Desactivar" : "Activar"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onEdit}
              disabled={busy}
              data-testid="sso-edit-button"
              aria-label="Editar"
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onDelete}
              disabled={busy}
              data-testid="sso-delete-button"
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
