"use client";

// ADR 0127 — botón «Conectar» del flujo OAuth de un MCP server remoto.
//
// Para servers cuyo `auth_kind === "oauth"` (p.ej. el remoto oficial de
// Atlassian, `mcp.atlassian.com`) NO hay token que pegar: el operador pulsa
// «Conectar» UNA vez, consiente en el proveedor y la plataforma refresca el
// token sola. Este componente es la parte de FRONTEND de ese flujo.
//
// Contrato con el backend (lo consume este componente; los endpoints son la
// contraparte a implementar):
//
//   GET  /projects/{id}/mcp-servers/{name}/oauth/status
//        → 200 { connected: boolean, expires_at: string|null, scopes?: string[] }
//
//   POST /projects/{id}/mcp-servers/{name}/oauth/connect
//        → 200 { authorization_url: string }
//        El front redirige el navegador a `authorization_url` (consentimiento
//        del proveedor). El server debe estar YA guardado (el endpoint lo
//        localiza por `name`), por eso el botón vive en la ficha del server.
//
//   GET  /projects/{id}/mcp-servers/{name}/oauth/callback?code&state   (backend)
//        Persiste los tokens en Vault y redirige de vuelta a esta página con
//        `?oauth_result=connected|error&server={name}[&reason=...]`, que
//        `page.tsx` lee para mostrar el banner y refrescar el estado.
//
// Mientras el backend no exista, «Conectar» devolverá un error tipado que
// mostramos con gracia (no rompe la página).

import { useMutation, useQuery } from "@tanstack/react-query";
import { KeyRound, Link2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";

export interface OAuthStatus {
  connected: boolean;
  expires_at: string | null;
  scopes?: string[];
}

export interface OAuthConnectResponse {
  authorization_url: string;
}

function statusPath(projectId: string, serverName: string): string {
  return `/projects/${projectId}/mcp-servers/${encodeURIComponent(serverName)}/oauth/status`;
}

function connectPath(projectId: string, serverName: string): string {
  return `/projects/${projectId}/mcp-servers/${encodeURIComponent(serverName)}/oauth/connect`;
}

export function McpOAuthConnect({
  projectId,
  serverName,
  providerLabel,
  // Inyectable para tests; en runtime redirige el navegador al consentimiento.
  onAuthorize = (url: string) => window.location.assign(url),
}: {
  projectId: string;
  serverName: string;
  providerLabel?: string;
  onAuthorize?: (url: string) => void;
}) {
  const statusQuery = useQuery({
    queryKey: ["mcp-oauth-status", projectId, serverName],
    queryFn: () => apiFetch<OAuthStatus>(statusPath(projectId, serverName)),
    // El estado OAuth no cambia solo; no reintentamos en bucle un endpoint que
    // quizá aún no exista (backend pendiente) — un fallo se trata como
    // "desconocido/desconectado" abajo, sin tumbar la card.
    retry: false,
    refetchOnWindowFocus: false,
  });

  const connectMutation = useMutation({
    mutationFn: () =>
      apiFetch<OAuthConnectResponse>(connectPath(projectId, serverName), { method: "POST" }),
    onSuccess: (data) => {
      if (data?.authorization_url) onAuthorize(data.authorization_url);
    },
  });

  const provider = providerLabel || "el proveedor";
  const connected = statusQuery.data?.connected === true;
  const statusUnavailable = statusQuery.isError; // endpoint aún no disponible / error
  const expiresAt = statusQuery.data?.expires_at ?? null;

  return (
    <div
      className="bg-muted/30 mt-3 rounded-md border p-3"
      data-testid={`mcp-oauth-connect-${serverName}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <KeyRound className="h-3.5 w-3.5" />
            Conexión OAuth
            {statusQuery.isLoading ? (
              <Badge variant="muted" data-testid={`mcp-oauth-status-loading-${serverName}`}>
                comprobando…
              </Badge>
            ) : connected ? (
              <Badge variant="success" data-testid={`mcp-oauth-status-connected-${serverName}`}>
                Conectado
              </Badge>
            ) : (
              <Badge variant="muted" data-testid={`mcp-oauth-status-disconnected-${serverName}`}>
                No conectado
              </Badge>
            )}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            {connected
              ? `Autorizado con ${provider}. La plataforma refresca el token automáticamente.`
              : `Autoriza el acceso a ${provider} una sola vez; la plataforma guardará y refrescará el token.`}
            {connected && expiresAt ? (
              <span data-testid={`mcp-oauth-expires-${serverName}`}> · caduca {expiresAt}</span>
            ) : null}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant={connected ? "outline" : "default"}
          onClick={() => connectMutation.mutate()}
          disabled={connectMutation.isPending}
          data-testid={`mcp-oauth-connect-button-${serverName}`}
        >
          {connectMutation.isPending ? (
            "Redirigiendo…"
          ) : connected ? (
            <>
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
              Reconectar
            </>
          ) : (
            <>
              <Link2 className="mr-1 h-3.5 w-3.5" />
              Conectar
            </>
          )}
        </Button>
      </div>

      {connectMutation.isError ? (
        <p
          className="text-destructive mt-2 whitespace-pre-wrap text-xs"
          data-testid={`mcp-oauth-connect-error-${serverName}`}
        >
          {connectMutation.error instanceof ApiError
            ? connectMutation.error.body
            : String(connectMutation.error)}
        </p>
      ) : statusUnavailable && !statusQuery.isLoading ? (
        <p
          className="text-muted-foreground mt-2 text-xs italic"
          data-testid={`mcp-oauth-status-unavailable-${serverName}`}
        >
          No se pudo consultar el estado de conexión (el flujo OAuth puede no estar disponible
          todavía).
        </p>
      ) : null}
    </div>
  );
}
