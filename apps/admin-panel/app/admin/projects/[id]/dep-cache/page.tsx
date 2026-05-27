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
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

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

  return (
    <div className="container mx-auto px-4 py-6">
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
          <table className="w-full text-sm" data-testid="dep-cache-table">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-2 pr-2">Runtime</th>
                <th className="py-2 pr-2">Lock file</th>
                <th className="py-2 pr-2">Acciones</th>
                <th className="py-2">Resultado</th>
              </tr>
            </thead>
            <tbody>
              {RUNTIMES.map((rt) => {
                const result = results[rt.id];
                const isLoading = invalidate.isPending && invalidate.variables === rt.id;
                return (
                  <tr key={rt.id} className="border-b" data-runtime={rt.id}>
                    <td className="py-2 pr-2 font-mono">{rt.label}</td>
                    <td className="py-2 pr-2 font-mono text-xs">{rt.lockFile}</td>
                    <td className="py-2 pr-2">
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
                    </td>
                    <td className="py-2">
                      {result && (
                        <span
                          className={result.ok ? "text-green-600" : "text-red-600"}
                          data-testid={`result-${rt.id}`}
                        >
                          {result.message}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
