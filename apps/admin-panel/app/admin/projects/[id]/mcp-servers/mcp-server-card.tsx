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
import {
  OAUTH_AUTH_KIND,
  TRANSPORT_BADGE,
  TRANSPORT_LABEL,
  type McpServerConfig,
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
          </CardTitle>
          <p className="text-muted-foreground mt-1 break-all font-mono text-xs">
            {server.transport === "stdio"
              ? `${server.command ?? ""} ${server.args.join(" ")}`.trim()
              : (server.url ?? "")}
          </p>
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
      {isOAuth && projectId ? (
        <CardContent className="pt-0">
          <McpOAuthConnect
            projectId={projectId}
            serverName={server.name}
            providerLabel={providerLabel}
          />
        </CardContent>
      ) : null}
    </Card>
  );
}
