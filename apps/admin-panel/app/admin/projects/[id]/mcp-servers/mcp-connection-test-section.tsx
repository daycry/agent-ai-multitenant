"use client";

/**
 * «Probar conexión» + importación selectiva de tools (task_05_07, task_06_18_12).
 *
 * Troceado de `mcp-server-dialog.tsx` en prod-16 `task_prod16_07`.
 *
 * **Se lleva siete `useState` del diálogo y no trae ninguno a cambio.** Todo el
 * estado de aquí —si está probando, el resultado, el error, qué tools ha marcado
 * el operador, si está importando— sólo lo usaba este bloque; vivía en el
 * diálogo por haber nacido ahí, no por hacer falta. Lo único que necesita de
 * fuera es `buildPayload`, la forma canónica del server que el formulario ya
 * construye para guardar: se prueba EXACTAMENTE lo que se va a guardar, no una
 * copia que se pueda desincronizar.
 *
 * La multiselección es deliberada (ADR 0052, supply chain): NO se importa todo
 * lo que el servidor expone. Se preseleccionan las tools descubiertas y el
 * operador desmarca las que no quiere en su catálogo.
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";
import { TestResultPanel, type TestConnectionResult } from "./mcp-test-result-panel";
import {
  MCP_EGRESS_BLOCKED,
  MCP_EGRESS_PROXY_UNAVAILABLE,
  mcpErrorCode,
  type McpServerConfig,
} from "./mcp-server-types";

export function McpConnectionTestSection({
  projectId,
  buildPayload,
  disabled,
}: {
  projectId: string;
  buildPayload: () => McpServerConfig;
  disabled: boolean;
}) {
  const errorText = useErrorText();
  const t = useT("mcpServers");
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  // task_mk_02 (ADR 0165 D9): el `error_code` tipado del fallo, para ramificar
  // los dos casos de egress con un texto accionable en vez del mensaje crudo.
  const [testErrorCode, setTestErrorCode] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  // task_06_18_12 (ADR 0052) — selección de tools a importar al catálogo.
  // Multiselección configurable por el operador: NO importamos todo, el
  // operador marca qué tools de terceros entran en su catálogo (supply chain).
  const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importedCount, setImportedCount] = useState<number | null>(null);

  async function handleTestConnection() {
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    setTestErrorCode(null);
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
      setTestError(errorText(err));
      setTestErrorCode(mcpErrorCode(err));
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
      setImportError(errorText(err));
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="border-muted rounded-md border p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">{t("testTitle")}</p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleTestConnection}
          disabled={testing || disabled || !buildPayload().name}
          data-testid="mcp-form-test"
        >
          {testing ? t("testing") : t("testButton")}
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
        <div className="mt-2 space-y-1" data-testid="mcp-form-test-error">
          {testErrorCode === MCP_EGRESS_BLOCKED ? (
            <EgressErrorNote
              title={t("egressBlockedTitle")}
              help={t("egressBlockedHelp")}
              testId="mcp-form-test-egress-blocked"
            />
          ) : testErrorCode === MCP_EGRESS_PROXY_UNAVAILABLE ? (
            <EgressErrorNote
              title={t("egressProxyUnavailableTitle")}
              help={t("egressProxyUnavailableHelp")}
              testId="mcp-form-test-egress-proxy-unavailable"
            />
          ) : null}
          <p className="text-destructive whitespace-pre-wrap text-xs">{testError}</p>
        </div>
      ) : (
        <p className="text-muted-foreground mt-2 text-xs">{t("testHelp")}</p>
      )}
    </div>
  );
}

/**
 * Los dos fallos de egress (ADR 0165 D9.2) con su texto accionable ENCIMA del
 * mensaje del backend, no en vez de él: el mensaje trae el host concreto y el
 * runbook; la nota dice qué hacer y, sobre todo, qué NO hacer (rotar un token
 * sano porque se leyó un 403 del proxy como un 403 del servidor).
 */
function EgressErrorNote({ title, help, testId }: { title: string; help: string; testId: string }) {
  return (
    <div
      className="bg-warning-soft text-warning-soft-foreground border-warning/30 rounded-md border p-2 text-xs"
      data-testid={testId}
    >
      <p className="font-medium">{title}</p>
      <p>{help}</p>
    </div>
  );
}
