"use client";

/**
 * task_06_12 — Botón "Invalidar caché" del dep-cache del proyecto.
 *
 * Tabla minimalista: una fila por runtime que usa el dep-cache, con un
 * botón para invalidar todas las entradas de ese runtime. La invalidación
 * llama a
 *
 *     POST /projects/{id}/dep-cache/invalidate
 *
 * con `{runtime: "..."}` (sin lock_hash → wipe all). El siguiente
 * test-run del runtime ese paga de nuevo el coste de instalación; este
 * panel es un escape hatch para cuando un operador sospecha que el
 * caché está corrupto.
 *
 * Plan 06.18 task_06_18_11: la lista de runtimes y sus etiquetas vienen de
 * `GET /runtime-templates` (el backend es la única fuente de verdad). Antes
 * esta pantalla hardcodeaba 12 runtimes con etiquetas + "lock files"
 * inventados que divergían de los 14 de la pantalla de Comandos. Ahora solo
 * mostramos los templates que realmente usan dep-cache (`dep_cache_mount`
 * no nulo) con su `label` ES+EN servido — los `generic-*` (sin caché) no
 * aparecen porque no tienen nada que invalidar.
 *
 * La pantalla no muestra entries existentes (sería otra petición a un
 * endpoint listing que aún no existe). Si en un futuro hace falta una
 * vista granular por hash, vive aquí.
 */

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { DataTable, type DataTableColumn } from "@/components/shared/data-table";
import { StateBlock } from "@/components/shared/state-block";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import {
  runtimeLabel,
  useRuntimeTemplates,
  type RuntimeTemplateDto,
} from "@/lib/runtime-templates";
import { cn } from "@/lib/utils";

interface InvalidateResponse {
  runtime: string;
  invalidated_count: number;
  invalidated_paths: string[];
}

interface ResultRow {
  runtime: string;
  ok: boolean;
  message: string;
}

export default function DepCachePage() {
  const params = useParams<{ id: string }>();
  const projectId = params?.id ?? "";
  const t = useT("depCache");
  const { lang } = useLang();
  const [results, setResults] = useState<Record<string, ResultRow>>({});

  const runtimesQuery = useRuntimeTemplates();
  // Only templates with a dep-cache mount can have something to invalidate;
  // generic-shell / generic-http opt out (dep_cache_mount === null).
  const cachedRuntimes = (runtimesQuery.data ?? []).filter((rt) => rt.dep_cache_mount !== null);

  const invalidate = useMutation<InvalidateResponse, ApiError, string>({
    mutationFn: async (runtime: string) => {
      const res = await apiFetch<InvalidateResponse>(
        `/projects/${projectId}/dep-cache/invalidate`,
        {
          method: "POST",
          body: { runtime },
        },
      );
      return res;
    },
    onSuccess: (data) => {
      setResults((prev) => ({
        ...prev,
        [data.runtime]: {
          runtime: data.runtime,
          ok: true,
          message:
            data.invalidated_count === 1
              ? t("invalidatedCountOne")
              : t("invalidatedCountMany", { n: data.invalidated_count }),
        },
      }));
    },
    onError: (err, runtime) => {
      setResults((prev) => ({
        ...prev,
        [runtime]: {
          runtime,
          ok: false,
          message: err.message,
        },
      }));
    },
  });

  const columns: DataTableColumn<RuntimeTemplateDto>[] = [
    {
      key: "runtime",
      header: t("colRuntime"),
      cell: (rt) => runtimeLabel(rt, lang),
    },
    {
      key: "mount",
      header: t("colMount"),
      className: "font-mono text-xs",
      cell: (rt) => rt.dep_cache_mount,
    },
    {
      key: "actions",
      header: t("colActions"),
      cell: (rt) => {
        const isLoading = invalidate.isPending && invalidate.variables === rt.id;
        return (
          <Button
            size="sm"
            variant="destructive"
            disabled={isLoading}
            onClick={() => invalidate.mutate(rt.id)}
            data-testid={`invalidate-${rt.id}`}
          >
            <Trash2 className="mr-1 h-3 w-3" />
            {isLoading ? t("invalidating") : t("invalidate")}
          </Button>
        );
      },
    },
    {
      key: "result",
      header: t("colResult"),
      cell: (rt) => {
        const result = results[rt.id];
        if (!result) return null;
        return (
          <span
            className={cn(
              "text-sm font-medium",
              result.ok ? "text-success-soft-foreground" : "text-danger-soft-foreground",
            )}
            data-testid={`result-${rt.id}`}
          >
            {result.message}
          </span>
        );
      },
    },
  ];

  return (
    <div className="container mx-auto px-4 py-6">
      <ProjectBreadcrumb projectId={projectId} current={t("title")} />
      <PageHeader title={t("title")} description={t("description")} />

      <Card>
        <CardHeader>
          <CardTitle>{t("cardTitle")}</CardTitle>
        </CardHeader>
        <CardContent>
          <StateBlock
            isLoading={runtimesQuery.isLoading}
            loadingSkeleton
            skeletonRows={4}
            loadingTestId="dep-cache-loading"
            isError={runtimesQuery.isError}
            error={runtimesQuery.error}
            errorTitle={t("errorTitle")}
            errorTestId="dep-cache-error"
          >
            <DataTable
              data={cachedRuntimes}
              data-testid="dep-cache-table"
              getRowKey={(rt) => rt.id}
              rowProps={(rt) => ({ "data-runtime": rt.id })}
              columns={columns}
            />
          </StateBlock>
        </CardContent>
      </Card>
    </div>
  );
}
