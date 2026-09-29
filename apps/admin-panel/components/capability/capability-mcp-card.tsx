"use client";

/**
 * Tarjeta de un servidor MCP en la pestaña Capacidades del proyecto (`task_ui_20`).
 *
 * Todo lo que enseña sale de `GET /projects/{id}/capabilities`: procedencia, conexión
 * OAuth, tools importadas y egress. Probar e Importar son los MISMOS actos directos de
 * `task_mk_01` (`McpServerCardActions`) y «Conectar» el flujo de `McpOAuthConnect`: no
 * hay una segunda implementación. Declarar, editar o borrar el servidor sigue siendo de
 * su pantalla de configuración (enlace en la tarjeta).
 */

import Link from "next/link";
import { Settings2 } from "lucide-react";

import { McpOAuthConnect } from "@/app/admin/projects/[id]/mcp-servers/mcp-oauth-connect";
import { McpServerCardActions } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-card-actions";
import type { McpServerConfig } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type McpServerCapability, provenanceOf } from "@/lib/capability/project-capabilities";
import { useT } from "@/lib/i18n";

export function CapabilityMcpCard({
  projectId,
  capability,
  config,
}: {
  projectId: string;
  capability: McpServerCapability;
  /** La configuración guardada del servidor (para Probar); `undefined` si no se pudo leer. */
  config: McpServerConfig | undefined;
}) {
  const t = useT("projectCapabilities");
  const provenance = provenanceOf(capability.origin);
  const { oauth, egress } = capability;

  return (
    <Card data-testid={`capability-card-${capability.name}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span className="truncate">{capability.name}</span>
            <Badge
              variant={provenance.kind === "marketplace" ? "info" : "muted"}
              data-testid={`capability-origin-${capability.name}`}
            >
              {provenance.kind === "marketplace"
                ? t("originDeployed", { listing: provenance.listing, version: provenance.version })
                : t("originDeclared")}
            </Badge>
            {oauth.required ? (
              <Badge
                variant={oauth.connected ? "success" : "warning"}
                data-testid={`capability-oauth-${capability.name}`}
              >
                {oauth.connected ? t("oauthConnected") : t("oauthNotConnected")}
              </Badge>
            ) : null}
            {egress.blocked ? (
              <Badge variant="warning" data-testid={`capability-egress-${capability.name}`}>
                {t("egressBlocked", { host: egress.host ?? "" })}
              </Badge>
            ) : null}
          </CardTitle>
          {capability.url ? (
            <p className="text-muted-foreground mt-1 break-all font-mono text-xs">
              {capability.url}
            </p>
          ) : null}
        </div>
        <Link
          href={`/admin/projects/${projectId}/mcp-servers`}
          className="text-muted-foreground hover:text-foreground inline-flex shrink-0 items-center gap-1 text-xs"
          data-testid={`capability-configure-${capability.name}`}
        >
          <Settings2 className="h-3.5 w-3.5" />
          {t("configure")}
        </Link>
      </CardHeader>
      <CardContent className="pt-0">
        {oauth.required ? (
          <McpOAuthConnect projectId={projectId} serverName={capability.name} />
        ) : null}
        {config ? (
          <McpServerCardActions
            projectId={projectId}
            server={config}
            importedCount={capability.imported_tools}
            disabled={false}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}
