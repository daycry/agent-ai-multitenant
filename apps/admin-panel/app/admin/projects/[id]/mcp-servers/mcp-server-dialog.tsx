"use client";

/**
 * Diálogo de crear/editar un MCP server: campos por transporte, plantillas del
 * catálogo y "probar conexión" con importación de tools (task_05_06/05_07).
 *
 * Troceado desde `mcp-server-sections.tsx` en prod-16 `task_prod16_08`.
 *
 * **Sigue por encima del techo de 500 líneas de una pieza, y es deliberado.**
 * Este componente es UN formulario con una decena de `useState` entrelazados
 * (estado del server, args en crudo, plantilla aplicada, escotilla de Vault,
 * resultado del test, selección de tools a importar). Partirlo de verdad exige
 * decidir cómo viaja ese estado —prop-drilling a cinco niveles o un contexto
 * local—, y eso no es un movimiento mecánico: es un rediseño con riesgo de
 * regresión en un formulario que ya funciona. Queda anotado en
 * `SECTION_ALLOWLIST` con su tamaño real (de 1125 a esto), que es deuda medida
 * y decreciente, no deuda escondida.
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
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
import {
  CATEGORY_LABEL,
  OAUTH_AUTH_KIND,
  templateToConfig,
  type McpCatalogEntry,
  type McpServerConfig,
  type Transport,
} from "./mcp-server-types";
import { TestResultPanel, type TestConnectionResult } from "./mcp-test-result-panel";

// Dialog form — create/edit one MCP server
// --------------------------------------------------------------------------
export function McpServerDialog({
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
  const [advancedOpen, setAdvancedOpen] = useState<boolean>(
    Boolean(initial.auth_ref) || initial.timeout_s !== 30,
  );
  // Tracks the catalog template the operator just applied (if any).
  // When set + the template declares secrets, we render a friendly
  // info card instead of the raw Vault path — the path lives in
  // `state.auth_ref` (already pre-rendered with the project UUID) and
  // travels to the backend on submit, but the user doesn't have to
  // see it. Cleared as soon as the user edits any field manually.
  const [appliedTemplate, setAppliedTemplate] = useState<McpCatalogEntry | null>(null);
  // Devops escape hatch — exposes the raw `vault:…` input when the
  // operator clicks "Detalles técnicos".
  const [showRawAuth, setShowRawAuth] = useState(false);
  // True when the dialog is opened for create (no name yet); the
  // template picker is only meaningful before the user starts typing.
  const isCreate = !initial.name;

  // Reset state when the dialog re-opens with different initial data.
  useEffect(() => {
    setState(initial);
    setArgsRaw(initial.args.join("\n"));
    setAdvancedOpen(Boolean(initial.auth_ref) || initial.timeout_s !== 30);
    setAppliedTemplate(null);
    setShowRawAuth(false);
  }, [initial]);

  // Catalog fetch (only when creating — editing existing skips it).
  const catalogQuery = useQuery({
    queryKey: ["mcp-catalog"],
    queryFn: () => apiFetch<McpCatalogEntry[]>("/mcp-catalog"),
    enabled: isCreate,
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
  });

  // For task_05_07 — the panel shows results below the form.
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  // task_06_18_12 (ADR 0052) — selección de tools a importar al catálogo.
  // Multiselección configurable por el operador: NO importamos todo, el
  // operador marca qué tools de terceros entran en su catálogo (supply chain).
  const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importedCount, setImportedCount] = useState<number | null>(null);

  const isStdio = state.transport === "stdio";

  function applyTemplate(templateId: string) {
    if (!templateId) return;
    const entry = (catalogQuery.data ?? []).find((t) => t.id === templateId);
    if (!entry) return;
    const next = templateToConfig(entry, projectId);
    setState(next);
    setArgsRaw(next.args.join("\n"));
    setAppliedTemplate(entry);
    setShowRawAuth(false);
    // If the template declares secrets, expand the advanced section so
    // the operator can see the credential card right away.
    if (entry.requires_auth) setAdvancedOpen(true);
  }

  // Any manual edit of auth_ref breaks the "managed by template"
  // invariant — drop the appliedTemplate marker so the raw input
  // takes over again.
  function setAuthRefManual(value: string) {
    setState({ ...state, auth_ref: value });
    if (appliedTemplate) setAppliedTemplate(null);
  }

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
    setImportError(null);
    setImportedCount(null);
    try {
      const result = await apiFetch<TestConnectionResult>(
        `/projects/${projectId}/mcp/test-connection`,
        {
          method: "POST",
          body: buildPayload(),
        },
      );
      setTestResult(result);
      // Pre-seleccionar todas las tools descubiertas — el operador puede
      // desmarcar las que no quiera importar (multiselección configurable).
      setSelectedTools(new Set(result.tools.map((t) => t.name)));
    } catch (err) {
      setTestError(err instanceof ApiError ? err.body : String(err));
    } finally {
      setTesting(false);
    }
  }

  function toggleSelectedTool(name: string) {
    setSelectedTools((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  async function handleImportTools() {
    // El nombre del server tal como se guarda en el proyecto — es el prefijo
    // de namespacing <server>.<tool> que el backend aplica.
    const serverName = buildPayload().name;
    if (!serverName || selectedTools.size === 0) return;
    setImporting(true);
    setImportError(null);
    setImportedCount(null);
    try {
      const result = await apiFetch<{ tools: { name: string }[] }>(
        `/projects/${projectId}/mcp/servers/${encodeURIComponent(serverName)}/import-tools`,
        {
          method: "POST",
          body: { tool_names: Array.from(selectedTools) },
        },
      );
      setImportedCount(result.tools.length);
    } catch (err) {
      setImportError(err instanceof ApiError ? err.body : String(err));
    } finally {
      setImporting(false);
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
            {/* Template picker — only when creating */}
            {isCreate && (
              <div className="bg-muted/30 -mx-2 rounded-md border p-3">
                <Label htmlFor="mcp-form-template">Plantilla rápida</Label>
                <select
                  id="mcp-form-template"
                  data-testid="mcp-form-template"
                  className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm"
                  defaultValue=""
                  onChange={(e) => applyTemplate(e.target.value)}
                  disabled={catalogQuery.isLoading}
                >
                  <option value="">
                    {catalogQuery.isLoading
                      ? "Cargando catálogo…"
                      : "— Elige una plantilla (opcional) —"}
                  </option>
                  {Object.entries(
                    (catalogQuery.data ?? []).reduce<Record<string, McpCatalogEntry[]>>(
                      (acc, entry) => {
                        const cat = entry.category;
                        if (!acc[cat]) acc[cat] = [];
                        acc[cat].push(entry);
                        return acc;
                      },
                      {},
                    ),
                  ).map(([cat, entries]) => (
                    <optgroup key={cat} label={CATEGORY_LABEL[cat] ?? cat}>
                      {entries.map((entry) => (
                        <option key={entry.id} value={entry.id}>
                          {entry.display_name}
                          {entry.requires_auth ? " 🔒" : ""}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                <p className="text-muted-foreground mt-1.5 text-xs">
                  Aplica una configuración verificada (GitHub, Jira, Google Drive, Slack, etc.). El
                  candado 🔒 indica que la integración necesita credenciales — el campo aparecerá en
                  Opciones avanzadas.
                </p>
              </div>
            )}

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

            {/* ADR 0127 — plantilla OAuth: no hay token que pegar. Se guarda el
                server y se conecta desde su ficha con «Conectar». */}
            {appliedTemplate?.auth_kind === OAUTH_AUTH_KIND ? (
              <div
                className="bg-info-soft text-info-soft-foreground rounded-md border border-info/30 p-3"
                data-testid="mcp-form-oauth-note"
              >
                <p className="text-sm font-medium">🔗 Este servidor se conecta por OAuth</p>
                <p className="mt-1 text-xs">
                  No necesitas pegar ningún token. <strong>Guarda</strong> el server y pulsa{" "}
                  <strong>«Conectar»</strong> en su ficha: te llevará a{" "}
                  {appliedTemplate.display_name} para autorizar una vez, y la plataforma refrescará
                  el token sola.
                </p>
              </div>
            ) : null}

            {/* Opciones avanzadas — colapsa auth + timeout */}
            <div className="border-t pt-3">
              <button
                type="button"
                onClick={() => setAdvancedOpen(!advancedOpen)}
                data-testid="mcp-form-advanced-toggle"
                aria-expanded={advancedOpen}
                className="text-muted-foreground hover:text-foreground flex w-full items-center justify-between text-sm font-medium transition-colors"
              >
                <span className="flex items-center gap-1.5">
                  {advancedOpen ? (
                    <ChevronDown className="h-4 w-4" />
                  ) : (
                    <ChevronRight className="h-4 w-4" />
                  )}
                  Opciones avanzadas
                </span>
                <span className="text-xs opacity-60">
                  {state.auth_ref ? "credencial • " : ""}timeout {state.timeout_s}s
                </span>
              </button>

              {advancedOpen && (
                <div className="mt-3 space-y-4">
                  {appliedTemplate?.requires_auth && !showRawAuth ? (
                    <div
                      className="bg-success-soft text-success-soft-foreground rounded-md border border-success/30 p-3"
                      data-testid="mcp-form-auth-managed"
                    >
                      <p className="text-sm font-medium">🔒 Esta integración requiere credencial</p>
                      <p className="mt-1 text-xs">
                        El sistema ya sabe dónde guardar el secreto. Pide al{" "}
                        <strong>administrador del tenant</strong> que añada{" "}
                        <code>{appliedTemplate.secret_keys.join(", ") || "la credencial"}</code> en
                        Vault antes del primer uso. Mientras no esté, las llamadas a este MCP
                        devolverán un error de autenticación tipado (no se cae el sistema).
                      </p>
                      <p className="mt-2 text-xs">
                        <a
                          href="https://github.com/daycry/agent-ai-multitenant/blob/master/docs/03-guides/configurar-mcp-server.md"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary underline-offset-2 hover:underline"
                        >
                          Ver guía de configuración →
                        </a>
                        {"  ·  "}
                        <button
                          type="button"
                          onClick={() => setShowRawAuth(true)}
                          className="text-muted-foreground hover:text-foreground underline-offset-2 hover:underline"
                          data-testid="mcp-form-show-raw-auth"
                        >
                          Detalles técnicos
                        </button>
                      </p>
                    </div>
                  ) : (
                    <div>
                      <div className="flex items-center justify-between">
                        <Label htmlFor="mcp-form-auth-ref">
                          {appliedTemplate?.requires_auth
                            ? "Ruta del secreto en Vault"
                            : "Credencial del servidor (opcional)"}
                        </Label>
                        {appliedTemplate?.requires_auth && showRawAuth && (
                          <button
                            type="button"
                            onClick={() => setShowRawAuth(false)}
                            className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
                            data-testid="mcp-form-hide-raw-auth"
                          >
                            ← Ocultar detalles técnicos
                          </button>
                        )}
                      </div>
                      <Input
                        id="mcp-form-auth-ref"
                        data-testid="mcp-form-auth-ref"
                        value={state.auth_ref ?? ""}
                        onChange={(e) => setAuthRefManual(e.target.value)}
                        placeholder="vault:secret/data/mcp/<servicio>/<proyecto>"
                      />
                      <p className="text-muted-foreground mt-1 text-xs">
                        {appliedTemplate?.requires_auth
                          ? "El sistema rellena esta ruta automáticamente al aplicar una plantilla. Solo edítala si tu Vault tiene una convención distinta."
                          : "Solo para MCPs que necesitan API key / token. El admin del tenant guarda el secreto en Vault y aquí solo se referencia con la ruta vault:…"}
                      </p>
                    </div>
                  )}

                  <div>
                    <Label htmlFor="mcp-form-timeout">Timeout (segundos)</Label>
                    <Input
                      id="mcp-form-timeout"
                      data-testid="mcp-form-timeout"
                      type="number"
                      min={1}
                      max={300}
                      value={state.timeout_s}
                      onChange={(e) =>
                        setState({ ...state, timeout_s: Number(e.target.value) || 30 })
                      }
                    />
                    <p className="text-muted-foreground mt-1 text-xs">
                      Tiempo máximo por llamada. 30s va bien para la mayoría; sube a 120s para MCPs
                      lentos como Docling o Puppeteer.
                    </p>
                  </div>
                </div>
              )}
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
                <TestResultPanel
                  result={testResult}
                  serverName={buildPayload().name}
                  selected={selectedTools}
                  onToggle={toggleSelectedTool}
                  onImport={handleImportTools}
                  importing={importing}
                  importError={importError}
                  importedCount={importedCount}
                />
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
