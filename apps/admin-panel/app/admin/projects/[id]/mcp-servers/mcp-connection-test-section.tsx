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
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { TestResultPanel, type TestConnectionResult } from "./mcp-test-result-panel";
import { type McpServerConfig } from "./mcp-server-types";

export function McpConnectionTestSection({
  projectId,
  buildPayload,
  disabled,
}: {
  projectId: string;
  buildPayload: () => McpServerConfig;
  disabled: boolean;
}) {
  const t = useT("mcpServers");
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
        <p
          className="text-destructive mt-2 whitespace-pre-wrap text-xs"
          data-testid="mcp-form-test-error"
        >
          {testError}
        </p>
      ) : (
        <p className="text-muted-foreground mt-2 text-xs">{t("testHelp")}</p>
      )}
    </div>
  );
}
