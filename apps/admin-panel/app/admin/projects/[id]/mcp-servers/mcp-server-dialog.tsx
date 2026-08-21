"use client";

/**
 * Diálogo de crear/editar un MCP server: campos por transporte y plantillas del
 * catálogo (task_05_06/05_07).
 *
 * Troceado desde `mcp-server-sections.tsx` (1125 líneas) en prod-16
 * `task_prod16_08`; repartido del todo en `task_prod16_07`.
 *
 * ## Por qué esto tardó dos olas, y qué estaba mal en el argumento de la primera
 *
 * El 2026-08-10 este fichero se dejó a propósito en 665 líneas, por encima del
 * techo de 500 de una pieza y anotado en `SECTION_ALLOWLIST` con su razón
 * escrita: era UN formulario con una decena de `useState` entrelazados, y
 * partirlo pedía decidir cómo viaja ese estado —prop-drilling a cinco niveles o
 * un contexto local—, o sea un rediseño con riesgo de regresión.
 *
 * El argumento valía para el corte que se estaba mirando, que era trocear el
 * formulario. No valía para el que había: **dos de esos bloques no compartían
 * estado con el formulario, lo tenían prestado por haber nacido aquí.**
 *
 *  - `mcp-connection-test-section.tsx` se llevó **siete** `useState` (probando,
 *    resultado, error, tools marcadas, importando, error de importación,
 *    importadas) y sólo necesita `buildPayload`: así se prueba exactamente lo
 *    que se va a guardar, no una copia que se pueda desincronizar.
 *  - `mcp-advanced-options-section.tsx` se llevó 112 líneas de JSX y
 *    `setAuthRefManual`. Su `showRawAuth` se queda AQUÍ porque el diálogo lo
 *    reinicia en dos sitios (al aplicar plantilla y al reabrirse); está
 *    argumentado en el docstring de esa sección.
 *
 * O sea que el corte **bajó** el número de `useState` de este fichero en vez de
 * repartirlos por la jerarquía, que era justo el riesgo que se quería evitar. Lo
 * que queda es un formulario indivisible de verdad: nombre, transporte, sus
 * campos y la plantilla aplicada se tocan entre sí.
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
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
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { McpAdvancedOptionsSection } from "./mcp-advanced-options-section";
import { McpConnectionTestSection } from "./mcp-connection-test-section";
import {
  CATEGORY_LABEL,
  OAUTH_AUTH_KIND,
  templateToConfig,
  type McpCatalogEntry,
  type McpServerConfig,
  type Transport,
} from "./mcp-server-types";

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
  const t = useT("mcpServers");
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

  function handleSubmit() {
    onSubmit(buildPayload());
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="mcp-server-dialog">
        <DialogHeader>
          <DialogTitle>{t("dialogTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {/* Template picker — only when creating */}
            {isCreate && (
              <div className="bg-muted/30 -mx-2 rounded-md border p-3">
                <Label htmlFor="mcp-form-template">{t("templateLabel")}</Label>
                <select
                  id="mcp-form-template"
                  data-testid="mcp-form-template"
                  className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm"
                  defaultValue=""
                  onChange={(e) => applyTemplate(e.target.value)}
                  disabled={catalogQuery.isLoading}
                >
                  <option value="">
                    {catalogQuery.isLoading ? t("templateLoading") : t("templateNone")}
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
                    <optgroup key={cat} label={CATEGORY_LABEL[cat] ? t(CATEGORY_LABEL[cat]) : cat}>
                      {entries.map((entry) => (
                        <option key={entry.id} value={entry.id}>
                          {entry.display_name}
                          {entry.requires_auth ? " 🔒" : ""}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                <p className="text-muted-foreground mt-1.5 text-xs">{t("templateHelp")}</p>
              </div>
            )}

            {/* Name */}
            <div>
              <Label htmlFor="mcp-form-name">{t("nameLabel")}</Label>
              <Input
                id="mcp-form-name"
                data-testid="mcp-form-name"
                value={state.name}
                onChange={(e) => setState({ ...state, name: e.target.value })}
                placeholder="github-mcp"
              />
              <p className="text-muted-foreground mt-1 text-xs">
                {t("nameHelp")} <code>_-.</code>.
              </p>
            </div>

            {/* Transport selector */}
            <div>
              <Label htmlFor="mcp-form-transport">{t("transportLabel")}</Label>
              <select
                id="mcp-form-transport"
                data-testid="mcp-form-transport"
                className="border-input bg-background ring-offset-background focus-visible:ring-ring h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                value={state.transport}
                onChange={(e) => setState({ ...state, transport: e.target.value as Transport })}
              >
                <option value="stdio">{t("transportStdio")}</option>
                {/* Los otros dos son el identificador del transporte tal cual
                    lo nombra el protocolo: no hay nada que traducir. */}
                <option value="sse">sse (HTTP server-sent events)</option>
                <option value="streamable_http">streamable_http</option>
              </select>
            </div>

            {/* Transport-specific fields */}
            {isStdio ? (
              <>
                <div>
                  <Label htmlFor="mcp-form-command">{t("commandLabel")}</Label>
                  <Input
                    id="mcp-form-command"
                    data-testid="mcp-form-command"
                    value={state.command ?? ""}
                    onChange={(e) => setState({ ...state, command: e.target.value })}
                    placeholder="docling-mcp"
                  />
                </div>
                <div>
                  <Label htmlFor="mcp-form-args">{t("argsLabel")}</Label>
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
                  label={t("envLabel")}
                  testIdPrefix="mcp-form-env"
                  emptyHint={t("envEmpty")}
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
                  label={t("headersLabel")}
                  testIdPrefix="mcp-form-headers"
                  emptyHint={t("headersEmpty")}
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
                <p className="text-sm font-medium">{t("oauthNoteTitle")}</p>
                <p className="mt-1 text-xs">
                  {t("oauthNoteIntro")} <strong>{t("oauthNoteSaveStrong")}</strong>{" "}
                  {t("oauthNoteMiddle")} <strong>{t("oauthNoteConnectStrong")}</strong>{" "}
                  {t("oauthNoteTail", { provider: appliedTemplate.display_name })}
                </p>
              </div>
            ) : null}

            <McpAdvancedOptionsSection
              state={state}
              onChange={setState}
              appliedTemplate={appliedTemplate}
              onManualAuthEdit={() => setAppliedTemplate(null)}
              open={advancedOpen}
              onOpenChange={setAdvancedOpen}
              showRawAuth={showRawAuth}
              onShowRawAuthChange={setShowRawAuth}
            />

            <McpConnectionTestSection
              projectId={projectId}
              buildPayload={buildPayload}
              disabled={submitting}
            />

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
            {t("cancel")}
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting || !state.name}
            data-testid="mcp-form-submit"
          >
            {submitting ? t("saving") : submitLabel}
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
  const t = useT("mcpServers");
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
          {t("kvAdd")}
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
                aria-label={t("kvKey")}
                data-testid={`${testIdPrefix}-key-${idx}`}
                value={key}
                onChange={(e) => update(key, e.target.value, value)}
                placeholder="KEY"
                className="flex-1"
              />
              <Input
                aria-label={t("kvValue")}
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
                aria-label={t("kvRemove")}
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
