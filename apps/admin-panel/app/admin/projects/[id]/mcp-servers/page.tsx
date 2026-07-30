"use client";

/**
 * task_05_06 — MCP servers configurados por proyecto.
 *
 * Lista la propiedad `mcp_servers` (JSONB) del proyecto y deja
 * añadir/editar/borrar entradas. La persistencia es vía
 * `PUT /projects/{id}` con el array reemplazado entero — la
 * validación Pydantic (api_server.mcp.config) se ejecuta en backend
 * y rechaza configuraciones inválidas (transport-specific fields,
 * unique names, auth_ref con prefijo vault:).
 *
 * El botón "Probar conexión" (task_05_07) vive dentro del dialog de
 * edición; abre un panel inline debajo del form mostrando los tools
 * descubiertos o el error tipado.
 *
 * Diseño de la pantalla: una card por server, con un dialog modal para
 * crear/editar. El editor de `env` / `headers` es una tabla de filas
 * key/value con botones para añadir y quitar — más test-friendly en
 * Playwright que un JSON textarea.
 */

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

import { McpServerCard, McpServerDialog, McpToolRolePolicySection } from "./mcp-server-sections";
import {
  authKindByUrl,
  emptyServer,
  type McpCatalogEntry,
  type McpServerConfig,
  type ProjectResponse,
} from "./mcp-server-types";

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectMcpServersPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const queryClient = useQueryClient();

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<ProjectResponse>(`/projects/${projectId}`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  // Catálogo → para saber, por la `url` de un server ya guardado, si usa OAuth
  // (el server config no persiste `auth_kind`) y su nombre de proveedor.
  const catalogQuery = useQuery({
    queryKey: ["mcp-catalog"],
    queryFn: () => apiFetch<McpCatalogEntry[]>("/mcp-catalog"),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
    enabled: Boolean(projectId),
  });
  const catalog = useMemo(() => catalogQuery.data ?? [], [catalogQuery.data]);
  const kindByUrl = useMemo(() => authKindByUrl(catalog), [catalog]);
  const nameByUrl = useMemo(() => {
    const out: Record<string, string> = {};
    for (const e of catalog) if (e.url) out[e.url] = e.display_name;
    return out;
  }, [catalog]);

  // ADR 0127 — retorno del callback OAuth: el backend redirige aquí con
  // `?oauth_result=connected|error&server={name}[&reason=...]`. Mostramos un
  // banner, refrescamos el estado de conexión de ese server y limpiamos la URL.
  const router = useRouter();
  const searchParams = useSearchParams();
  const oauthResult = searchParams.get("oauth_result");
  const oauthServer = searchParams.get("server");
  const oauthReason = searchParams.get("reason");
  useEffect(() => {
    if (!oauthResult) return;
    if (oauthServer) {
      void queryClient.invalidateQueries({
        queryKey: ["mcp-oauth-status", projectId, oauthServer],
      });
    }
    // Quita los params para que un refresco no re-muestre el banner.
    router.replace(`/admin/projects/${projectId}/mcp-servers`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oauthResult, oauthServer, projectId]);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<McpServerConfig | null>(null);
  // Index of the entry being edited, -1 when creating a new one.
  const [editingIndex, setEditingIndex] = useState<number>(-1);

  const servers = projectQuery.data?.mcp_servers ?? [];

  const saveMutation = useMutation({
    mutationFn: (next: McpServerConfig[]) =>
      apiFetch<ProjectResponse>(`/projects/${projectId}`, {
        method: "PUT",
        body: { mcp_servers: next },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      setDialogOpen(false);
      setEditing(null);
      setEditingIndex(-1);
    },
  });

  function handleAdd() {
    setEditing(emptyServer());
    setEditingIndex(-1);
    setDialogOpen(true);
  }

  function handleEdit(server: McpServerConfig, index: number) {
    setEditing({ ...server });
    setEditingIndex(index);
    setDialogOpen(true);
  }

  function handleDelete(index: number) {
    if (!window.confirm("¿Borrar este MCP server del proyecto?")) return;
    const next = servers.filter((_, i) => i !== index);
    saveMutation.mutate(next);
  }

  function handleSave(updated: McpServerConfig) {
    const next =
      editingIndex >= 0
        ? servers.map((s, i) => (i === editingIndex ? updated : s))
        : [...servers, updated];
    saveMutation.mutate(next);
  }

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="project-mcp-page"
    >
      <ProjectBreadcrumb projectId={projectId} current="MCP servers" />
      <PageHeader
        icon={<Plug className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="MCP servers del proyecto"
        description="Servidores MCP (Model Context Protocol) que los agentes de este proyecto podrán usar como tools."
        data-testid="project-mcp-header"
        actions={
          <Button onClick={handleAdd} data-testid="mcp-add-button">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Añadir MCP server
          </Button>
        }
      />

      {/* ADR 0127 — resultado del flujo OAuth «Conectar» al volver del proveedor. */}
      {oauthResult === "connected" ? (
        <div
          className="bg-success-soft text-success-soft-foreground mt-6 rounded-md border border-success/30 p-3 text-sm"
          data-testid="mcp-oauth-banner-connected"
        >
          ✓ Conexión OAuth completada{oauthServer ? ` para «${oauthServer}»` : ""}. El token quedó
          guardado y se refrescará automáticamente.
        </div>
      ) : oauthResult === "error" ? (
        <div
          className="bg-danger-soft text-danger-soft-foreground mt-6 rounded-md border border-danger/30 p-3 text-sm"
          data-testid="mcp-oauth-banner-error"
        >
          No se pudo completar la conexión OAuth{oauthServer ? ` de «${oauthServer}»` : ""}.
          {oauthReason ? ` (${oauthReason})` : ""} Vuelve a intentarlo con «Conectar».
        </div>
      ) : null}

      {projectQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm">Cargando…</p>
      ) : projectQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="project-mcp-error">
          {projectQuery.error instanceof ApiError
            ? projectQuery.error.body
            : String(projectQuery.error)}
        </p>
      ) : servers.length === 0 ? (
        <Card className="mt-6">
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="project-mcp-empty">
              Este proyecto aún no tiene MCP servers configurados. Pulsa{" "}
              <strong>“Añadir MCP server”</strong> para declarar el primero.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="mt-6 space-y-3">
          {servers.map((server, index) => (
            <McpServerCard
              key={`${server.name}-${index}`}
              server={server}
              onEdit={() => handleEdit(server, index)}
              onDelete={() => handleDelete(index)}
              busy={saveMutation.isPending}
              projectId={projectId}
              authKind={server.url ? kindByUrl[server.url] : undefined}
              providerLabel={server.url ? nameByUrl[server.url] : undefined}
            />
          ))}
        </div>
      )}

      {saveMutation.isError ? (
        <p className="text-destructive mt-3 text-xs" data-testid="project-mcp-save-error">
          {saveMutation.error instanceof ApiError
            ? saveMutation.error.body
            : String(saveMutation.error)}
        </p>
      ) : null}

      {/* ADR 0128 fase 4: política OPCIONAL rol→tool de las MCP del proyecto. */}
      {!projectQuery.isLoading && !projectQuery.isError ? (
        <McpToolRolePolicySection projectId={projectId} />
      ) : null}

      {editing ? (
        <McpServerDialog
          projectId={projectId}
          open={dialogOpen}
          onOpenChange={(next) => {
            if (!saveMutation.isPending) setDialogOpen(next);
          }}
          initial={editing}
          submitLabel={editingIndex >= 0 ? "Guardar cambios" : "Crear"}
          submitting={saveMutation.isPending}
          onSubmit={handleSave}
          // Submit-time validation errors from the backend surface here
          // so the dialog can highlight the right field.
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
