"use client";

/**
 * task_04_06 / task_ui_31 — Memoria del Equipo.
 *
 * Pantalla que el operador usa para inspeccionar lo que el Memorizer
 * (task_04_03) y el endpoint `POST /memories` (task_04_05) han escrito en el
 * store. Pestañas por ámbito con contador, ficha de procedencia en cada
 * memoria (quién, cuándo, dónde, recuperaciones), buscador semántico, alta
 * manual y borrado (soft-delete).
 *
 * Permisos:
 *   - cualquier usuario del tenant lee (RLS); las privadas, sólo su dueño
 *     (el backend las filtra: el contador de cada pestaña es el del listado),
 *   - cualquier usuario crea private / team_shared / project_shared,
 *   - sólo tenant_admin puede crear global (el backend devuelve 403).
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Search } from "lucide-react";

import { CreateMemoryCard } from "@/components/memories/create-memory-card";
import { MemoryList } from "@/components/memories/memory-list";
import {
  SCOPE_KEY,
  type MemoryCounts,
  type MemoryResponse,
  type MemoryScope,
  type MemorySearchHit,
} from "@/components/memories/types";
import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

const SCOPE_OPTIONS: (MemoryScope | "all")[] = [
  "all",
  "private",
  "team_shared",
  "project_shared",
  "global",
];

export default function MemoriesPage() {
  const t = useT("memories");
  const tp = useT("memoriesProvenance");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [scopeFilter, setScopeFilter] = useState<MemoryScope | "all">("all");
  const [draft, setDraft] = useState("");
  const [term, setTerm] = useState("");
  const searching = term.length > 0;
  const scopeParam = scopeFilter === "all" ? "" : `&scope=${scopeFilter}`;

  const listQuery = useQuery({
    queryKey: ["memories", scopeFilter],
    queryFn: () =>
      apiFetch<MemoryResponse[]>(
        scopeFilter === "all" ? "/memories" : `/memories?scope=${scopeFilter}`,
      ),
    refetchOnWindowFocus: false,
  });

  const countsQuery = useQuery({
    queryKey: ["memories", "counts"],
    queryFn: () => apiFetch<MemoryCounts>("/memories/counts"),
    refetchOnWindowFocus: false,
  });
  // Un backend anterior (o un mock) puede no devolver el objeto: sin contador, no se inventa.
  const counts =
    countsQuery.data && !Array.isArray(countsQuery.data) && typeof countsQuery.data === "object"
      ? countsQuery.data
      : null;

  const searchQuery = useQuery({
    queryKey: ["memories", "search", term, scopeFilter],
    queryFn: () =>
      apiFetch<MemorySearchHit[]>(`/memories/search?q=${encodeURIComponent(term)}${scopeParam}`),
    enabled: searching,
    refetchOnWindowFocus: false,
  });

  const active = searching ? searchQuery : listQuery;
  const memories: MemoryResponse[] = searching
    ? (searchQuery.data ?? []).map((h) => h.memory)
    : (listQuery.data ?? []);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["memories"] });

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8" data-testid="memories-page">
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="memories-header"
      />

      <CreateMemoryCard onCreated={invalidate} />

      <Card className="mt-6">
        <CardHeader className="flex flex-col gap-3">
          <div className="flex flex-row items-center justify-between gap-4">
            <CardTitle>
              {t(SCOPE_KEY[scopeFilter])} (
              {searching ? memories.length : (counts?.[scopeFilter] ?? "…")})
            </CardTitle>
            <div
              className="border-muted bg-muted/40 inline-flex rounded-md border p-0.5 text-xs"
              data-testid="memories-scope-filter"
              role="group"
              aria-label={tp("tabsAria")}
            >
              {SCOPE_OPTIONS.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => setScopeFilter(opt)}
                  data-testid={`memories-scope-${opt}`}
                  aria-pressed={scopeFilter === opt}
                  className={
                    "rounded px-2 py-1 transition-colors " +
                    (scopeFilter === opt
                      ? "bg-background shadow-sm font-semibold"
                      : "text-muted-foreground hover:text-foreground")
                  }
                >
                  {t(SCOPE_KEY[opt])}
                  {counts ? (
                    <span className="ml-1 tabular-nums" data-testid={`memories-count-${opt}`}>
                      {counts[opt]}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          </div>
          <form
            className="flex gap-2"
            data-testid="memories-search-form"
            onSubmit={(e) => {
              e.preventDefault();
              setTerm(draft.trim());
            }}
          >
            <Input
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                if (e.target.value.trim() === "") setTerm("");
              }}
              placeholder={tp("searchPlaceholder")}
              aria-label={tp("searchLabel")}
              data-testid="memories-search-input"
            />
            <Button type="submit" variant="outline" data-testid="memories-search-submit">
              <Search className="mr-1 h-4 w-4" />
              {tp("searchSubmit")}
            </Button>
          </form>
        </CardHeader>
        <CardContent>
          {active.isLoading ? (
            <p className="text-muted-foreground text-sm">
              {searching ? tp("searching") : tCommon("loading")}
            </p>
          ) : active.isError ? (
            <p className="text-destructive text-sm" data-testid="memories-error">
              {errorText(active.error)}
            </p>
          ) : memories.length === 0 ? (
            <p className="text-muted-foreground text-sm italic" data-testid="memories-empty">
              {searching ? tp("searchEmpty") : t("empty")}
            </p>
          ) : (
            <MemoryList memories={memories} onDeleted={invalidate} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
