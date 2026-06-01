"use client";

/**
 * task_06_12 — Botón "Invalidar caché" del dep-cache del proyecto.
 *
 * Tabla minimalista: una fila por runtime conocido, con un botón para
 * invalidar todas las entradas de ese runtime. La invalidación llama a
 *
 *     POST /projects/{id}/dep-cache/invalidate
 *
 * con `{runtime: "..."}` (sin lock_hash → wipe all). El siguiente
 * test-run del runtime ese paga de nuevo el coste de instalación; este
 * panel es un escape hatch para cuando un operador sospecha que el
 * caché está corrupto.
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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

// Mirror del catálogo en shared_test_runtimes/catalog.py — el orden
// matchea el de CATALOG (insertion order).
const RUNTIMES: Array<{ id: string; label: string; lockFile: string }> = [
  { id: "python-pytest", label: "Python · pytest", lockFile: "requirements.txt" },
  { id: "node-jest", label: "Node · Jest", lockFile: "package-lock.json" },
  { id: "node-vitest", label: "Node · Vitest", lockFile: "package-lock.json" },
  { id: "node-playwright", label: "Node · Playwright", lockFile: "package-lock.json" },
  { id: "php-phpunit", label: "PHP · PHPUnit", lockFile: "composer.lock" },
  { id: "php-pest", label: "PHP · Pest", lockFile: "composer.lock" },
  { id: "go-test", label: "Go · go test", lockFile: "go.sum" },
  { id: "java-maven", label: "Java · Maven", lockFile: "pom.xml" },
  { id: "java-gradle", label: "Java · Gradle", lockFile: "gradle.lockfile" },
  { id: "ruby-rspec", label: "Ruby · RSpec", lockFile: "Gemfile.lock" },
  { id: "rust-cargo", label: "Rust · Cargo", lockFile: "Cargo.lock" },
  { id: "dotnet-test", label: ".NET · dotnet test", lockFile: "packages.lock.json" },
];

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
  const [results, setResults] = useState<Record<string, ResultRow>>({});

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
          message: `${data.invalidated_count} entradas invalidadas`,
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

  const columns: DataTableColumn<(typeof RUNTIMES)[number]>[] = [
    {
      key: "runtime",
      header: "Runtime",
      className: "font-mono",
      cell: (rt) => rt.label,
    },
    {
      key: "lock",
      header: "Lock file",
      className: "font-mono text-xs",
      cell: (rt) => rt.lockFile,
    },
    {
      key: "actions",
      header: "Acciones",
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
            {isLoading ? "Invalidando..." : "Invalidar"}
          </Button>
        );
      },
    },
    {
      key: "result",
      header: "Resultado",
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
      <ProjectBreadcrumb projectId={projectId} current="Caché de dependencias" />
      <PageHeader
        title="Caché de dependencias"
        description={
          "Invalida la caché del dep-cache para forzar al worker-test a " +
          "reinstalar las dependencias en el siguiente run. Útil cuando " +
          "sospechas que la caché está corrupta."
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Runtimes con caché</CardTitle>
        </CardHeader>
        <CardContent>
          <DataTable
            data={RUNTIMES}
            data-testid="dep-cache-table"
            getRowKey={(rt) => rt.id}
            rowProps={(rt) => ({ "data-runtime": rt.id })}
            columns={columns}
          />
        </CardContent>
      </Card>
    </div>
  );
}
