"use client";

/**
 * Cuerpo de la pantalla de proveedores LLM: la tabla, sus acciones por fila y
 * el cableado de los dos diálogos.
 *
 * Extraído de `page.tsx` en prod-16 `task_prod16_08`. Refactor mecánico: mismos
 * `data-testid`, mismas queries y mismas invalidaciones de caché.
 *
 * Ninguna columna muestra el valor de una credencial: sólo el booleano
 * `has_credential` que devuelve la API (ADR 0028).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  KeyRound,
  Pencil,
  PlugZap,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";

import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { CopilotDeviceFlowDialog } from "./copilot-device-flow-dialog";
import {
  isKind,
  KIND_BADGE,
  KIND_LABEL,
  TEST_STATUS_KEY,
  type LlmProvider,
  type ProviderTestResult,
} from "./llm-provider-types";
import { ProviderFormDialog } from "./provider-form-dialog";

export function LlmProvidersContent() {
  const t = useT("llmProviders");
  const errorText = useErrorText();
  const queryClient = useQueryClient();

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<LlmProvider | null>(null);
  const [deviceFlowTarget, setDeviceFlowTarget] = useState<LlmProvider | null>(null);
  // Per-provider test result, keyed by provider id.
  const [testResults, setTestResults] = useState<Record<string, ProviderTestResult>>({});
  // Per-provider model-sync message, keyed by provider id.
  const [syncResults, setSyncResults] = useState<Record<string, string>>({});

  const listQuery = useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => apiFetch<LlmProvider[]>("/admin/llm-providers"),
    refetchOnWindowFocus: false,
  });

  const testMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<ProviderTestResult>(`/admin/llm-providers/${id}/test`, { method: "POST" }),
    onSuccess: (result, id) => {
      setTestResults((prev) => ({ ...prev, [id]: result }));
    },
    onError: (err, id) => {
      setTestResults((prev) => ({
        ...prev,
        [id]: { ok: false, status: "upstream_error", detail: errorText(err) },
      }));
    },
  });

  // Sync the provider's available models (POST /{id}/sync-models) into
  // config.models, so the assistant model selector reflects what the provider
  // actually serves (ADR 0053).
  const syncMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<{ models: string[]; count: number }>(`/admin/llm-providers/${id}/sync-models`, {
        method: "POST",
      }),
    onSuccess: (result, id) => {
      setSyncResults((prev) => ({ ...prev, [id]: t("syncedCount", { n: result.count }) }));
      // Refresh every provider+model dropdown so the freshly-synced models show
      // up without a manual page reload: the assistant (tenant + platform
      // default) and the platform-defaults agent-model surface all read the
      // provider's config.models.
      void queryClient.invalidateQueries({ queryKey: ["assistant-model-options"] });
      void queryClient.invalidateQueries({ queryKey: ["assistant-default-model-options"] });
      void queryClient.invalidateQueries({ queryKey: ["platform-settings", "model-options"] });
    },
    onError: (err, id) => {
      setSyncResults((prev) => ({ ...prev, [id]: errorText(err) }));
    },
  });

  // Quick active toggle (PUT is_active) without opening the dialog.
  const toggleMutation = useMutation({
    mutationFn: (p: LlmProvider) =>
      apiFetch<LlmProvider>(`/admin/llm-providers/${p.id}`, {
        method: "PUT",
        body: { is_active: !p.is_active },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/admin/llm-providers/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    },
  });

  const rows = listQuery.data ?? [];

  return (
    <>
      <div className="mt-6 flex justify-end">
        <Button size="sm" onClick={() => setCreateOpen(true)} data-testid="provider-create-open">
          <Plus className="mr-1 h-3.5 w-3.5" />
          {t("create")}
        </Button>
      </div>

      <div className="mt-4">
        <StateBlock
          isLoading={listQuery.isLoading}
          isError={listQuery.isError}
          error={listQuery.error}
          isEmpty={rows.length === 0}
          loadingLabel={t("loading")}
          loadingTestId="providers-loading"
          errorTestId="providers-error"
          empty={
            <Card>
              <CardContent className="py-10 text-center">
                <p className="text-muted-foreground text-sm italic" data-testid="providers-empty">
                  {t("empty")}
                </p>
              </CardContent>
            </Card>
          }
        >
          <div className="rounded-xl border" data-testid="providers-table">
            <Table>
              <TableHeader className="bg-muted">
                <TableRow>
                  <TableHead className="px-3">{t("colKind")}</TableHead>
                  <TableHead className="px-3">{t("colSlug")}</TableHead>
                  <TableHead className="px-3">{t("colName")}</TableHead>
                  <TableHead className="px-3">{t("endpoint")}</TableHead>
                  <TableHead className="px-3">{t("colCredential")}</TableHead>
                  <TableHead className="px-3">{t("colStatus")}</TableHead>
                  <TableHead className="px-3">{t("colConnection")}</TableHead>
                  <TableHead className="px-3 text-right">{t("colActions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((p) => {
                  const kindLabel = isKind(p.kind) ? KIND_LABEL[p.kind] : p.kind;
                  const result = testResults[p.id];
                  const isTesting = testMutation.isPending && testMutation.variables === p.id;
                  const isSyncing = syncMutation.isPending && syncMutation.variables === p.id;
                  const syncMsg = syncResults[p.id];
                  const statusKey = result ? TEST_STATUS_KEY[result.status] : undefined;
                  return (
                    <TableRow key={p.id} data-testid={`provider-row-${p.id}`}>
                      <TableCell className="px-3">
                        <Badge variant={KIND_BADGE[p.kind] ?? "muted"}>{kindLabel}</Badge>
                      </TableCell>
                      <TableCell className="px-3 font-mono text-xs">{p.slug}</TableCell>
                      <TableCell className="px-3 font-medium">{p.display_name}</TableCell>
                      <TableCell className="px-3 font-mono text-xs">{p.base_url ?? "—"}</TableCell>
                      <TableCell className="px-3" data-testid={`provider-credential-${p.id}`}>
                        {p.has_credential ? (
                          <Badge variant="success">
                            <KeyRound className="mr-1 h-3 w-3" />
                            {t("credentialSet")}
                          </Badge>
                        ) : (
                          <Badge variant="muted">{t("credentialUnset")}</Badge>
                        )}
                      </TableCell>
                      <TableCell className="px-3">
                        <button
                          type="button"
                          onClick={() => toggleMutation.mutate(p)}
                          disabled={toggleMutation.isPending}
                          className="focus-visible:ring-ring focus-visible:ring-offset-background cursor-pointer rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                          data-testid={`provider-toggle-${p.id}`}
                          aria-label={p.is_active ? t("deactivateProvider") : t("activateProvider")}
                          aria-pressed={p.is_active}
                        >
                          {p.is_active ? (
                            <Badge variant="success">{t("active")}</Badge>
                          ) : (
                            <Badge variant="muted">{t("inactive")}</Badge>
                          )}
                        </button>
                      </TableCell>
                      <TableCell className="px-3" data-testid={`provider-test-cell-${p.id}`}>
                        {isTesting ? (
                          <span className="text-muted-foreground text-xs">{t("testing")}</span>
                        ) : result ? (
                          <span
                            className="inline-flex items-center gap-1 text-xs"
                            data-testid={`provider-test-result-${p.id}`}
                            data-ok={result.ok ? "true" : "false"}
                            title={result.detail}
                          >
                            {result.ok ? (
                              <CheckCircle2 className="text-success h-3.5 w-3.5" />
                            ) : (
                              <XCircle className="text-destructive h-3.5 w-3.5" />
                            )}
                            {statusKey ? t(statusKey) : result.status}
                          </span>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </TableCell>
                      <TableCell className="px-3">
                        <div className="flex flex-col items-end gap-1">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => testMutation.mutate(p.id)}
                              disabled={isTesting}
                              data-testid={`provider-test-${p.id}`}
                              aria-label={t("testConnection")}
                            >
                              <PlugZap className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => syncMutation.mutate(p.id)}
                              disabled={isSyncing}
                              data-testid={`provider-sync-models-${p.id}`}
                              aria-label={t("syncModels")}
                              title={t("syncModelsTitle")}
                            >
                              <RefreshCw
                                className={`h-3.5 w-3.5 ${isSyncing ? "animate-spin" : ""}`}
                              />
                            </Button>
                            {p.kind === "copilot" ? (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setDeviceFlowTarget(p)}
                                data-testid={`provider-device-flow-${p.id}`}
                                aria-label={t("authorizeDeviceFlow")}
                              >
                                <KeyRound className="h-3.5 w-3.5" />
                              </Button>
                            ) : null}
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setEditTarget(p)}
                              data-testid={`provider-edit-${p.id}`}
                              aria-label={t("edit")}
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => deleteMutation.mutate(p.id)}
                              disabled={deleteMutation.isPending}
                              data-testid={`provider-delete-${p.id}`}
                              aria-label={t("delete")}
                            >
                              <Trash2 className="text-destructive h-3.5 w-3.5" />
                            </Button>
                          </div>
                          {syncMsg ? (
                            <span
                              className="text-muted-foreground text-xs"
                              data-testid={`provider-sync-result-${p.id}`}
                            >
                              {syncMsg}
                            </span>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </StateBlock>

        {toggleMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="provider-toggle-error">
            {errorText(toggleMutation.error)}
          </p>
        ) : null}
        {deleteMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="provider-delete-error">
            {errorText(deleteMutation.error)}
          </p>
        ) : null}
      </div>

      {createOpen ? (
        <ProviderFormDialog
          mode="create"
          onClose={() => setCreateOpen(false)}
          onSaved={() => {
            setCreateOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
          }}
        />
      ) : null}

      {editTarget ? (
        <ProviderFormDialog
          mode="edit"
          provider={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => {
            setEditTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
          }}
        />
      ) : null}

      {deviceFlowTarget ? (
        <CopilotDeviceFlowDialog
          provider={deviceFlowTarget}
          onClose={() => setDeviceFlowTarget(null)}
          onAuthorized={() => {
            setDeviceFlowTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
          }}
        />
      ) : null}
    </>
  );
}
