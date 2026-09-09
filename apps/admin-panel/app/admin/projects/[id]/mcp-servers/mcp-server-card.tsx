"use client";

/**
 * Tarjeta de un MCP server del proyecto.
 *
 * Sale de `mcp-server-sections.tsx` en prod-16 `task_prod16_08`: aquel fichero
 * era el resultado del tramo de modularización #9, que sacó 1125 líneas del
 * `page.tsx` y las dejó JUNTAS. Mover el bulto no es partir — y la guarda de
 * tamaño lo dejó dicho durante semanas en un comentario, que es la forma de
 * vigilancia que no vigila.
 */

import { Pencil, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useT } from "@/lib/i18n";
import { McpOAuthConnect } from "./mcp-oauth-connect";
import { McpServerCardActions } from "./mcp-server-card-actions";
import {
  OAUTH_AUTH_KIND,
  TRANSPORT_BADGE,
  TRANSPORT_LABEL,
  type McpServerConfig,
  type McpServerEgressWarning,
} from "./mcp-server-types";

// --------------------------------------------------------------------------
// Card — one MCP server entry
// --------------------------------------------------------------------------
export function McpServerCard({
  server,
  onEdit,
  onDelete,
  busy,
  projectId,
  authKind,
  providerLabel,
  egressWarning,
  importedCount,
}: {
  server: McpServerConfig;
  onEdit: () => void;
  onDelete: () => void;
  busy: boolean;
  // ADR 0127: cuando el server usa OAuth (`authKind === "oauth"`, resuelto por
  // page.tsx casando la url contra el catálogo) la ficha muestra el botón
  // «Conectar» en vez de una credencial en Vault.
  projectId?: string;
  authKind?: string;
  providerLabel?: string;
  // task_mk_02 (ADR 0165 D11): el aviso del backend cuando el host externo del
  // servidor aún no está en la allowlist de egress. Se pinta en la PÁGINA (esta
  // tarjeta), no en el diálogo, porque el diálogo se cierra al guardar.
  egressWarning?: McpServerEgressWarning;
  // ADR 0166 D2/D4 (task_mk_01): filas `<server>.*` vivas en el catálogo
  // (`null` mientras carga; `undefined` = la página no lo calcula, sin acciones).
  importedCount?: number | null;
}) {
  const t = useT("mcpServers");
  const isOAuth = authKind === OAUTH_AUTH_KIND;
  return (
    <Card data-testid={`mcp-server-card-${server.name}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <span className="truncate">{server.name}</span>
            <Badge variant={TRANSPORT_BADGE[server.transport]}>
              {TRANSPORT_LABEL[server.transport]}
            </Badge>
            {isOAuth ? (
              <Badge variant="info" data-testid={`mcp-server-oauth-${server.name}`}>
                OAuth
              </Badge>
            ) : server.auth_ref ? (
              <Badge variant="muted" data-testid={`mcp-server-auth-${server.name}`}>
                vault
              </Badge>
            ) : null}
            {egressWarning ? (
              <Badge variant="warning" data-testid={`mcp-server-egress-badge-${server.name}`}>
                {t("egressWarningBadge")}
              </Badge>
            ) : null}
          </CardTitle>
          <p className="text-muted-foreground mt-1 break-all font-mono text-xs">
            {server.transport === "stdio"
              ? `${server.command ?? ""} ${server.args.join(" ")}`.trim()
              : (server.url ?? "")}
          </p>
          {egressWarning ? (
            <p
              className="text-warning-soft-foreground mt-1 text-xs"
              title={egressWarning.message}
              data-testid={`mcp-server-egress-warning-${server.name}`}
            >
              {t("egressWarning", { host: egressWarning.host })}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            onClick={onEdit}
            disabled={busy}
            data-testid={`mcp-server-edit-${server.name}`}
            aria-label={t("edit")}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onDelete}
            disabled={busy}
            data-testid={`mcp-server-delete-${server.name}`}
            aria-label={t("delete")}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
      {(isOAuth && projectId) || (projectId && importedCount !== undefined) ? (
        <CardContent className="pt-0">
          {isOAuth ? (
            <McpOAuthConnect
              projectId={projectId}
              serverName={server.name}
              providerLabel={providerLabel}
            />
          ) : null}
          {importedCount !== undefined ? (
            <McpServerCardActions
              projectId={projectId}
              server={server}
              importedCount={importedCount}
              disabled={busy}
            />
          ) : null}
        </CardContent>
      ) : null}
    </Card>
  );
}
