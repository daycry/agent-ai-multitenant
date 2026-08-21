"use client";

/**
 * Ollama & Embeddings — System Admin surface (ADR 0056, option U-B).
 *
 * The governed, in-product alternative to bundling Open WebUI. Two sections:
 *
 *   1. Embeddings (read-only discovery, GET /admin/embeddings/available-models):
 *      shows the ACTIVE embedding model, Ollama reachability, which installed
 *      models are valid embedders + whether they are 768-compatible, and the
 *      recommended models to pull. The model itself is fixed via env
 *      (API_SERVER_EMBEDDING_MODEL) at install time — this is informational
 *      (changing it with existing KBs is the Plan 12 re-embed job).
 *
 *   2. Modelos Ollama (GET/POST/DELETE /admin/ollama/models): list installed
 *      models with their size, pull a new model by name, delete one.
 *
 * Whole screen is System-Admin only — the backend gates every endpoint with
 * require_system_admin; this mirrors it with a RoleGuard.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Download,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { RoleGuard } from "@/components/ui/role-guard";
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

// ---------------------------------------------------------------------------
// Types — mirror api_server.routers.embeddings + api_server.routers.ollama.
// ---------------------------------------------------------------------------
interface InstalledEmbedder {
  name: string;
  dim: number;
  compatible: boolean;
  active: boolean;
}

interface EmbeddingModelsResponse {
  ollama_reachable: boolean;
  active_model: string;
  required_dim: number;
  installed: InstalledEmbedder[];
  recommended: string[];
}

interface OllamaModel {
  name: string;
  size_bytes: number | null;
  modified_at: string | null;
}

interface OllamaModelsResponse {
  ollama_reachable: boolean;
  models: OllamaModel[];
}

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function OllamaPage() {
  const t = useT("ollama");
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8" data-testid="ollama-page">
      <PageHeader
        icon={<Sparkles className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="ollama-header"
      />
      <RoleGuard
        min="system_admin"
        fallback={
          <Card className="mt-6" data-testid="ollama-forbidden">
            <CardContent className="flex items-center gap-3 py-10">
              <ShieldAlert className="text-muted-foreground h-5 w-5 shrink-0" />
              <p className="text-muted-foreground text-sm">{t("forbidden")}</p>
            </CardContent>
          </Card>
        }
      >
        <div className="mt-6 space-y-8">
          <EmbeddingsSection />
          <ModelsSection />
        </div>
      </RoleGuard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 1 — Embeddings (read-only discovery).
// ---------------------------------------------------------------------------
function EmbeddingsSection() {
  const t = useT("ollama");
  const query = useQuery({
    queryKey: ["embeddings-available"],
    queryFn: () => apiFetch<EmbeddingModelsResponse>("/admin/embeddings/available-models"),
    refetchOnWindowFocus: false,
  });

  return (
    <section data-testid="embeddings-section">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">{t("embeddingsHeading")}</h2>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
          data-testid="embeddings-refresh"
        >
          <RefreshCw className={`mr-1 h-3.5 w-3.5 ${query.isFetching ? "animate-spin" : ""}`} />
          {t("refresh")}
        </Button>
      </div>
      <StateBlock
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        loadingLabel={t("loadingEmbeddings")}
      >
        {query.data ? <EmbeddingsBody data={query.data} /> : null}
      </StateBlock>
    </section>
  );
}

function EmbeddingsBody({ data }: { data: EmbeddingModelsResponse }) {
  const t = useT("ollama");
  return (
    <Card>
      <CardContent className="space-y-4 py-6">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-muted-foreground">{t("activeModel")}</span>
          <Badge variant="default" data-testid="embeddings-active">
            {data.active_model}
          </Badge>
          <span className="text-muted-foreground">{t("requiredDim")}</span>
          <span className="font-mono">{data.required_dim}</span>
          {data.ollama_reachable ? (
            <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
              <CheckCircle2 className="h-4 w-4" /> {t("reachable")}
            </span>
          ) : (
            <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
              <XCircle className="h-4 w-4" /> {t("unreachable")}
            </span>
          )}
        </div>

        {data.ollama_reachable && data.installed.length > 0 ? (
          <div className="rounded-xl border" data-testid="embeddings-installed">
            <Table>
              <TableHeader className="bg-muted">
                <TableRow>
                  <TableHead className="px-3">{t("colEmbedder")}</TableHead>
                  <TableHead className="px-3">{t("colDim")}</TableHead>
                  <TableHead className="px-3">{t("colCompatible")}</TableHead>
                  <TableHead className="px-3">{t("colActive")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.installed.map((m) => (
                  <TableRow key={m.name}>
                    <TableCell className="px-3 font-mono text-xs">{m.name}</TableCell>
                    <TableCell className="px-3">{m.dim}</TableCell>
                    <TableCell className="px-3">
                      {m.compatible ? (
                        <Badge variant="success">{t("yes")}</Badge>
                      ) : (
                        <Badge variant="muted">{t("no")}</Badge>
                      )}
                    </TableCell>
                    <TableCell className="px-3">{m.active ? "★" : ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm italic">
            {data.ollama_reachable ? t("noEmbeddersInstalled") : t("embeddersUnreachable")}
          </p>
        )}

        <div>
          <p className="text-muted-foreground mb-1 text-xs">{t("recommendedHelp")}</p>
          <div className="flex flex-wrap gap-1.5" data-testid="embeddings-recommended">
            {data.recommended.map((name) => (
              <Badge key={name} variant="info" className="font-mono text-[11px]">
                {name}
              </Badge>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 2 — Ollama model management (list / pull / delete).
// ---------------------------------------------------------------------------
function ModelsSection() {
  const t = useT("ollama");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [pullName, setPullName] = useState("");
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["ollama-models"],
    queryFn: () => apiFetch<OllamaModelsResponse>("/admin/ollama/models"),
    refetchOnWindowFocus: false,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["ollama-models"] });
    void queryClient.invalidateQueries({ queryKey: ["embeddings-available"] });
  };

  const pullMutation = useMutation({
    mutationFn: (name: string) =>
      apiFetch<{ ok: boolean; detail: string }>("/admin/ollama/models/pull", {
        method: "POST",
        body: { name },
      }),
    onSuccess: (res) => {
      setActionMsg(res.detail);
      setPullName("");
      invalidate();
    },
    onError: (err) => setActionMsg(errorText(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) =>
      apiFetch<{ ok: boolean; detail: string }>("/admin/ollama/models", {
        method: "DELETE",
        body: { name },
      }),
    onSuccess: (res) => {
      setActionMsg(res.detail);
      invalidate();
    },
    onError: (err) => setActionMsg(errorText(err)),
  });

  const busy = pullMutation.isPending || deleteMutation.isPending;

  return (
    <section data-testid="models-section">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold">{t("modelsHeading")}</h2>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
          data-testid="models-refresh"
        >
          <RefreshCw className={`mr-1 h-3.5 w-3.5 ${query.isFetching ? "animate-spin" : ""}`} />
          {t("refresh")}
        </Button>
      </div>

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-3 py-4">
          <div className="grow">
            <label htmlFor="pull-name" className="text-muted-foreground mb-1 block text-xs">
              {t("pullLabel")}
            </label>
            <Input
              id="pull-name"
              placeholder={t("pullPlaceholder")}
              value={pullName}
              onChange={(e) => setPullName(e.target.value)}
              data-testid="pull-input"
            />
          </div>
          <Button
            onClick={() => pullMutation.mutate(pullName.trim())}
            disabled={busy || pullName.trim().length === 0}
            data-testid="pull-button"
          >
            <Download
              className={`mr-1 h-3.5 w-3.5 ${pullMutation.isPending ? "animate-pulse" : ""}`}
            />
            {pullMutation.isPending ? t("pulling") : t("pull")}
          </Button>
        </CardContent>
      </Card>

      {actionMsg ? (
        <p className="text-muted-foreground mb-3 text-sm" data-testid="models-action-msg">
          {actionMsg}
        </p>
      ) : null}

      <StateBlock
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        loadingLabel={t("loadingModels")}
      >
        {query.data && !query.data.ollama_reachable ? (
          <Card>
            <CardContent className="flex items-center gap-3 py-8">
              <XCircle className="h-5 w-5 shrink-0 text-amber-500" />
              <p className="text-muted-foreground text-sm">{t("modelsUnreachable")}</p>
            </CardContent>
          </Card>
        ) : query.data && query.data.models.length === 0 ? (
          <Card>
            <CardContent className="py-8 text-center">
              <p className="text-muted-foreground text-sm italic" data-testid="models-empty">
                {t("modelsEmpty")}
              </p>
            </CardContent>
          </Card>
        ) : query.data ? (
          <div className="rounded-xl border" data-testid="models-table">
            <Table>
              <TableHeader className="bg-muted">
                <TableRow>
                  <TableHead className="px-3">{t("colModel")}</TableHead>
                  <TableHead className="px-3">{t("colSize")}</TableHead>
                  <TableHead className="px-3 text-right">{t("colActions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.models.map((m) => (
                  <TableRow key={m.name}>
                    <TableCell className="px-3 font-mono text-xs">{m.name}</TableCell>
                    <TableCell className="px-3">{formatBytes(m.size_bytes)}</TableCell>
                    <TableCell className="px-3 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deleteMutation.mutate(m.name)}
                        disabled={busy}
                        data-testid={`delete-${m.name}`}
                        aria-label={t("deleteAria", { name: m.name })}
                      >
                        <Trash2 className="h-3.5 w-3.5 text-red-500" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : null}
      </StateBlock>
    </section>
  );
}
