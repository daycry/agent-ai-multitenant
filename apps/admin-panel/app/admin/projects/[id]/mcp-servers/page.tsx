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
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plug, Plus, Trash2, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types (mirror api_server.mcp.config.MCPServerConfigModel)
// --------------------------------------------------------------------------
type Transport = "stdio" | "sse" | "streamable_http";

interface McpServerConfig {
  name: string;
  transport: Transport;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  auth_ref: string | null;
  timeout_s: number;
}

interface ProjectResponse {
  id: string;
  name: string;
  mcp_servers: McpServerConfig[];
  // ...other Project fields exist; we don't touch them
}

const TRANSPORT_LABEL: Record<Transport, string> = {
  stdio: "stdio (subprocess)",
  sse: "sse (HTTP stream)",
  streamable_http: "streamable_http",
};

const TRANSPORT_BADGE: Record<Transport, BadgeVariant> = {
  stdio: "info",
  sse: "warning",
  streamable_http: "success",
};

// Empty MCPServerConfigModel — used to seed the dialog form for "create".
function emptyServer(): McpServerConfig {
  return {
    name: "",
    transport: "stdio",
    command: "",
    args: [],
    env: {},
    url: null,
    headers: {},
    auth_ref: null,
    timeout_s: 30,
  };
}

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

// --------------------------------------------------------------------------
// Card — one MCP server entry
// --------------------------------------------------------------------------
function McpServerCard({
  server,
  onEdit,
  onDelete,
  busy,
}: {
  server: McpServerConfig;
  onEdit: () => void;
  onDelete: () => void;
  busy: boolean;
}) {
  return (
    <Card data-testid={`mcp-server-card-${server.name}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <span className="truncate">{server.name}</span>
            <Badge variant={TRANSPORT_BADGE[server.transport]}>
              {TRANSPORT_LABEL[server.transport]}
            </Badge>
            {server.auth_ref ? (
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
            aria-label="Editar"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onDelete}
            disabled={busy}
            data-testid={`mcp-server-delete-${server.name}`}
            aria-label="Eliminar"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardHeader>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Dialog form — create/edit one MCP server
// --------------------------------------------------------------------------
function McpServerDialog({
  projectId,
  open,
  onOpenChange,
  initial,
  submitLabel,
  submitting,
  onSubmit,
  backendError,
}: {
  projectId: string;
  open: boolean;
  onOpenChange: (next: boolean) => void;
  initial: McpServerConfig;
  submitLabel: string;
  submitting: boolean;
  onSubmit: (server: McpServerConfig) => void;
  backendError: string | null;
}) {
  const [state, setState] = useState<McpServerConfig>(initial);
  const [argsRaw, setArgsRaw] = useState<string>(initial.args.join("\n"));

  // Reset state when the dialog re-opens with different initial data.
  useEffect(() => {
    setState(initial);
    setArgsRaw(initial.args.join("\n"));
  }, [initial]);

  // For task_05_07 — the panel shows results below the form.
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  const isStdio = state.transport === "stdio";

  // Build the canonical server shape used by both Save and Probar.
  const buildPayload = useMemo(
    () => () => {
      const args = argsRaw
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean);
      const payload: McpServerConfig = {
        ...state,
        args: isStdio ? args : [],
        command: isStdio ? state.command || null : null,
        env: isStdio ? state.env : {},
        url: !isStdio ? state.url || null : null,
        headers: !isStdio ? state.headers : {},
        auth_ref: state.auth_ref?.trim() ? state.auth_ref.trim() : null,
      };
      return payload;
    },
    [state, argsRaw, isStdio],
  );

  async function handleTestConnection() {
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const result = await apiFetch<TestConnectionResult>(
        `/projects/${projectId}/mcp/test-connection`,
        {
          method: "POST",
          body: buildPayload(),
        },
      );
      setTestResult(result);
    } catch (err) {
      setTestError(err instanceof ApiError ? err.body : String(err));
    } finally {
      setTesting(false);
    }
  }

  function handleSubmit() {
    onSubmit(buildPayload());
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="mcp-server-dialog">
        <DialogHeader>
          <DialogTitle>Configurar MCP server</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {/* Name */}
            <div>
              <Label htmlFor="mcp-form-name">Nombre</Label>
              <Input
                id="mcp-form-name"
                data-testid="mcp-form-name"
                value={state.name}
                onChange={(e) => setState({ ...state, name: e.target.value })}
                placeholder="github-mcp"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Identificador del server dentro del proyecto. Solo letras, números, <code>_-.</code>
                .
              </p>
            </div>

            {/* Transport selector */}
            <div>
              <Label htmlFor="mcp-form-transport">Transporte</Label>
              <select
                id="mcp-form-transport"
                data-testid="mcp-form-transport"
                className="border-input bg-background ring-offset-background focus-visible:ring-ring h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                value={state.transport}
                onChange={(e) => setState({ ...state, transport: e.target.value as Transport })}
              >
                <option value="stdio">stdio (subprocess local)</option>
                <option value="sse">sse (HTTP server-sent events)</option>
                <option value="streamable_http">streamable_http</option>
              </select>
            </div>

            {/* Transport-specific fields */}
            {isStdio ? (
              <>
                <div>
                  <Label htmlFor="mcp-form-command">Comando</Label>
                  <Input
                    id="mcp-form-command"
                    data-testid="mcp-form-command"
                    value={state.command ?? ""}
                    onChange={(e) => setState({ ...state, command: e.target.value })}
                    placeholder="docling-mcp"
                  />
                </div>
                <div>
                  <Label htmlFor="mcp-form-args">Argumentos (uno por línea)</Label>
                  <textarea
                    id="mcp-form-args"
                    data-testid="mcp-form-args"
                    className="border-input bg-background ring-offset-background focus-visible:ring-ring min-h-[80px] w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                    value={argsRaw}
                    onChange={(e) => setArgsRaw(e.target.value)}
                    placeholder={"--transport\nstdio"}
                  />
                </div>
                <KeyValueEditor
                  label="Variables de entorno"
                  testIdPrefix="mcp-form-env"
                  emptyHint="No hay variables. Pulsa “Añadir” para declarar una."
                  entries={state.env}
                  onChange={(env) => setState({ ...state, env })}
                />
              </>
            ) : (
              <>
                <div>
                  <Label htmlFor="mcp-form-url">URL</Label>
                  <Input
                    id="mcp-form-url"
                    data-testid="mcp-form-url"
                    value={state.url ?? ""}
                    onChange={(e) => setState({ ...state, url: e.target.value })}
                    placeholder="https://github-mcp.example/mcp"
                  />
                </div>
                <KeyValueEditor
                  label="Cabeceras"
                  testIdPrefix="mcp-form-headers"
                  emptyHint="No hay cabeceras. Pulsa “Añadir” para declarar una."
                  entries={state.headers}
                  onChange={(headers) => setState({ ...state, headers })}
                />
              </>
            )}

            {/* auth_ref */}
            <div>
              <Label htmlFor="mcp-form-auth-ref">Auth (Vault pointer)</Label>
              <Input
                id="mcp-form-auth-ref"
                data-testid="mcp-form-auth-ref"
                value={state.auth_ref ?? ""}
                onChange={(e) => setState({ ...state, auth_ref: e.target.value })}
                placeholder="vault:secret/data/mcp/github/proj-42"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                Opcional. Si lo configuras, debe empezar por <code>vault:</code>. El secreto
                resuelto al conectar se inyecta como env (stdio) o headers (http).
              </p>
            </div>

            {/* Timeout */}
            <div>
              <Label htmlFor="mcp-form-timeout">Timeout (segundos)</Label>
              <Input
                id="mcp-form-timeout"
                data-testid="mcp-form-timeout"
                type="number"
                min={1}
                max={300}
                value={state.timeout_s}
                onChange={(e) => setState({ ...state, timeout_s: Number(e.target.value) || 30 })}
              />
            </div>

            {/* Probar conexión */}
            <div className="border-muted rounded-md border p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-medium">Probar conexión</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleTestConnection}
                  disabled={testing || submitting || !state.name}
                  data-testid="mcp-form-test"
                >
                  {testing ? "Probando…" : "Probar"}
                </Button>
              </div>
              {testResult ? (
                <TestResultPanel result={testResult} />
              ) : testError ? (
                <p
                  className="text-destructive mt-2 whitespace-pre-wrap text-xs"
                  data-testid="mcp-form-test-error"
                >
                  {testError}
                </p>
              ) : (
                <p className="text-muted-foreground mt-2 text-xs">
                  Abre una sesión one-shot contra el servidor y lista las tools que expone. No
                  guarda nada.
                </p>
              )}
            </div>

            {backendError ? (
              <p
                className="text-destructive whitespace-pre-wrap text-xs"
                data-testid="mcp-form-backend-error"
              >
                {backendError}
              </p>
            ) : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
            data-testid="mcp-form-cancel"
          >
            Cancelar
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting || !state.name}
            data-testid="mcp-form-submit"
          >
            {submitting ? "Guardando…" : submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------------------------
// Key/value editor — reusable for env (stdio) and headers (http)
// --------------------------------------------------------------------------
function KeyValueEditor({
  label,
  testIdPrefix,
  emptyHint,
  entries,
  onChange,
}: {
  label: string;
  testIdPrefix: string;
  emptyHint: string;
  entries: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  // Stable ordering — Object.entries() preserves insertion order so
  // the rows don't reshuffle when the user types.
  const rows = Object.entries(entries);

  function update(oldKey: string, newKey: string, newValue: string) {
    const next: Record<string, string> = {};
    for (const [k, v] of rows) {
      if (k === oldKey) {
        if (newKey) next[newKey] = newValue;
      } else {
        next[k] = v;
      }
    }
    onChange(next);
  }

  function add() {
    // Use a placeholder unique key the user can rename.
    let i = 1;
    while (entries[`KEY_${i}`] !== undefined) i += 1;
    onChange({ ...entries, [`KEY_${i}`]: "" });
  }

  function remove(key: string) {
    const next = { ...entries };
    delete next[key];
    onChange(next);
  }

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <Label>{label}</Label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={add}
          data-testid={`${testIdPrefix}-add`}
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          Añadir
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="text-muted-foreground text-xs italic" data-testid={`${testIdPrefix}-empty`}>
          {emptyHint}
        </p>
      ) : (
        <ul className="space-y-1.5" data-testid={`${testIdPrefix}-list`}>
          {rows.map(([key, value], idx) => (
            <li key={`${idx}-${key}`} className="flex items-center gap-1.5">
              <Input
                aria-label="key"
                data-testid={`${testIdPrefix}-key-${idx}`}
                value={key}
                onChange={(e) => update(key, e.target.value, value)}
                placeholder="KEY"
                className="flex-1"
              />
              <Input
                aria-label="value"
                data-testid={`${testIdPrefix}-value-${idx}`}
                value={value}
                onChange={(e) => update(key, key, e.target.value)}
                placeholder="value"
                className="flex-1"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => remove(key)}
                data-testid={`${testIdPrefix}-remove-${idx}`}
                aria-label="Quitar"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Test connection — result shape + panel (task_05_07)
// --------------------------------------------------------------------------
interface DiscoveredTool {
  name: string;
  description: string | null;
}

interface TestConnectionResult {
  server_name: string;
  server_version: string;
  server_instructions: string | null;
  tools: DiscoveredTool[];
}

function TestResultPanel({ result }: { result: TestConnectionResult }) {
  return (
    <div className="mt-2 space-y-2" data-testid="mcp-form-test-result">
      <p className="text-xs">
        Conectado a{" "}
        <strong data-testid="mcp-form-test-server-name">
          {result.server_name || "(sin nombre)"}
        </strong>
        {result.server_version ? (
          <>
            {" "}
            v<span data-testid="mcp-form-test-server-version">{result.server_version}</span>
          </>
        ) : null}
        {" — "}
        <span data-testid="mcp-form-test-tool-count">{result.tools.length}</span> tool
        {result.tools.length === 1 ? "" : "s"}.
      </p>
      {result.tools.length > 0 ? (
        <ul
          className="border-muted bg-muted/30 max-h-40 space-y-1 overflow-auto rounded border p-2 text-xs"
          data-testid="mcp-form-test-tools-list"
        >
          {result.tools.map((tool) => (
            <li key={tool.name} data-testid={`mcp-form-test-tool-${tool.name}`}>
              <code className="font-mono">{tool.name}</code>
              {tool.description ? (
                <span className="text-muted-foreground"> — {tool.description}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {result.server_instructions ? (
        <p
          className="text-muted-foreground whitespace-pre-wrap text-xs"
          data-testid="mcp-form-test-instructions"
        >
          {result.server_instructions}
        </p>
      ) : null}
    </div>
  );
}
