"use client";

/**
 * Resultado de "probar conexión" contra un MCP server: forma de la respuesta y
 * el panel que la pinta con las tools descubiertas (task_05_07).
 *
 * Troceado desde `mcp-server-sections.tsx` en prod-16 `task_prod16_08`.
 */

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

// --------------------------------------------------------------------------
// Test connection — result shape + panel (task_05_07)
// --------------------------------------------------------------------------
export interface DiscoveredTool {
  name: string;
  description: string | null;
}

export interface TestConnectionResult {
  server_name: string;
  server_version: string;
  server_instructions: string | null;
  tools: DiscoveredTool[];
}

export function TestResultPanel({
  result,
  serverName,
  selected,
  onToggle,
  onImport,
  importing,
  importError,
  importedCount,
}: {
  result: TestConnectionResult;
  // Nombre del server tal como se guardará en el proyecto — es el prefijo de
  // namespacing <server>.<tool> que el backend aplica al importar (ADR 0052).
  serverName: string;
  selected: Set<string>;
  onToggle: (name: string) => void;
  onImport: () => void;
  importing: boolean;
  importError: string | null;
  importedCount: number | null;
}) {
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
            <li
              key={tool.name}
              data-testid={`mcp-form-test-tool-${tool.name}`}
              className="flex items-start gap-2"
            >
              {/* Multiselección: el operador marca qué tools importar. */}
              <input
                type="checkbox"
                className="mt-0.5"
                checked={selected.has(tool.name)}
                onChange={() => onToggle(tool.name)}
                data-testid={`mcp-form-import-select-${tool.name}`}
                aria-label={`Seleccionar ${tool.name}`}
              />
              <span className="min-w-0">
                {/* Faceta Origen=MCP: el server como prefijo/badge para que
                    <server>.read_file no parezca un duplicado de read_file. */}
                <Badge variant="info" className="mr-1 align-middle">
                  {serverName || "mcp"}
                </Badge>
                <code className="font-mono">{tool.name}</code>
                {tool.description ? (
                  <span className="text-muted-foreground"> — {tool.description}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {result.tools.length > 0 ? (
        <div className="flex items-center justify-between gap-2">
          <Button
            type="button"
            size="sm"
            onClick={onImport}
            disabled={importing || selected.size === 0 || !serverName}
            data-testid="mcp-form-import-button"
          >
            {importing
              ? "Importando…"
              : `Importar ${selected.size} tool${selected.size === 1 ? "" : "s"} al catálogo`}
          </Button>
          {importedCount !== null ? (
            <span className="text-success text-xs" data-testid="mcp-form-import-success">
              Importadas {importedCount} al catálogo (Origen MCP, nivel “Aislada”).
            </span>
          ) : null}
        </div>
      ) : null}
      {importError ? (
        <p
          className="text-destructive whitespace-pre-wrap text-xs"
          data-testid="mcp-form-import-error"
        >
          {importError}
        </p>
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
