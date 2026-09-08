"use client";

/**
 * Recuento e importación de tools de un servidor MCP, EN LA TARJETA (ADR 0166 D2,
 * `task_mk_01`).
 *
 * Hasta aquí «Probar conexión» e «Importar» vivían sólo dentro del diálogo de
 * edición: la tarjeta no decía cuántas tools había importadas, ni si había cero,
 * y el estado invisible es el que nadie arregla (MK-02). Ahora la tarjeta dice
 * «N tools importadas» o «sin importar» —un COUNT de filas `<server>.*` del
 * catálogo, el estado DERIVADO de D4— y ofrece los dos actos directos:
 *
 * - **Probar**: `POST /mcp/test-connection` con el servidor tal como está
 *   guardado; enseña cuántas tools anuncia o el error tipado.
 * - **Importar**: `POST /mcp/servers/{name}/import-tools` SIN `tool_names`, es
 *   decir «todas las que anuncie ahora» con reconciliación (R3) y tope (L1). La
 *   multiselección del ADR 0052 sigue en el diálogo.
 *
 * Toda ruta automática (despliegue, «Conectar») es un atajo sobre ESTE botón, que
 * permanece disponible aunque la cola de la lane `marketplace` no se drene.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { type McpServerConfig, mcpErrorCode } from "./mcp-server-types";

interface ImportResponse {
  tools: { name: string }[];
  retired: string[];
  omitted: string[];
  warnings: string[];
}

interface TestResponse {
  server_name: string;
  tools: { name: string }[];
}

export function McpServerCardActions({
  projectId,
  server,
  importedCount,
  disabled,
}: {
  projectId: string;
  server: McpServerConfig;
  /** Filas `<server>.*` vivas en el catálogo; `null` mientras el catálogo carga. */
  importedCount: number | null;
  disabled: boolean;
}) {
  const t = useT("mcpServers");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [testSummary, setTestSummary] = useState<string | null>(null);

  const test = useMutation<TestResponse, unknown>({
    mutationFn: () =>
      apiFetch<TestResponse>(`/projects/${projectId}/mcp/test-connection`, {
        method: "POST",
        body: server,
      }),
    onSuccess: (r) => setTestSummary(t("cardTestOk", { count: r.tools.length })),
    onError: () => setTestSummary(null),
  });

  const importAll = useMutation<ImportResponse, unknown>({
    mutationFn: () =>
      apiFetch<ImportResponse>(
        `/projects/${projectId}/mcp/servers/${encodeURIComponent(server.name)}/import-tools`,
        { method: "POST", body: {} },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tools-catalog"] });
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  const busy = disabled || test.isPending || importAll.isPending;

  return (
    <div className="mt-2 space-y-2" data-testid={`mcp-server-actions-${server.name}`}>
      <div className="flex flex-wrap items-center gap-2">
        {importedCount === null ? null : importedCount > 0 ? (
          <Badge variant="success" data-testid={`mcp-server-imported-${server.name}`}>
            {t("cardImportedCount", { count: importedCount })}
          </Badge>
        ) : (
          <Badge variant="warning" data-testid={`mcp-server-not-imported-${server.name}`}>
            {t("cardNotImported")}
          </Badge>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => test.mutate()}
          disabled={busy}
          data-testid={`mcp-server-test-${server.name}`}
        >
          {test.isPending ? t("testing") : t("testButton")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => importAll.mutate()}
          disabled={busy}
          data-testid={`mcp-server-import-${server.name}`}
        >
          {importAll.isPending ? t("importing") : t("cardImportAll")}
        </Button>
      </div>

      {testSummary && !test.isError ? (
        <p
          className="text-muted-foreground text-xs"
          data-testid={`mcp-server-test-ok-${server.name}`}
        >
          {testSummary}
        </p>
      ) : null}
      {test.isError ? (
        <p
          className="text-destructive text-xs"
          data-testid={`mcp-server-test-error-${server.name}`}
        >
          {errorText(test.error)}
        </p>
      ) : null}

      {importAll.isSuccess ? (
        <div className="text-xs" data-testid={`mcp-server-import-result-${server.name}`}>
          <p className="text-muted-foreground">
            {t("cardImportDone", {
              count: importAll.data.tools.length,
              retired: importAll.data.retired.length,
            })}
          </p>
          {importAll.data.warnings.length > 0 ? (
            <ul className="text-warning-soft-foreground mt-1 list-disc pl-4">
              {importAll.data.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {importAll.isError ? (
        <p
          className="text-destructive text-xs"
          data-testid={`mcp-server-import-error-${server.name}`}
        >
          {mcpErrorCode(importAll.error) === "TOO_MANY_TOOLS"
            ? t("cardImportTooMany")
            : mcpErrorCode(importAll.error) === "OAUTH_NOT_CONNECTED"
              ? t("cardImportOauthNotConnected")
              : errorText(importAll.error)}
        </p>
      ) : null}
    </div>
  );
}
