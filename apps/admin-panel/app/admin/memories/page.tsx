"use client";

/**
 * task_04_06 — Memoria del Equipo.
 *
 * Pantalla que el operador usa para inspeccionar lo que el Memorizer
 * (task_04_03) y el endpoint `POST /memories` (task_04_05) han escrito
 * en el store. Permite filtrar por scope/type, crear una memoria
 * manual y borrar (soft-delete) las existentes.
 *
 * Permisos:
 *   - cualquier usuario del tenant lee (RLS),
 *   - cualquier usuario crea private / team_shared / project_shared,
 *   - sólo tenant_admin puede crear global (el backend devuelve 403).
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, GitMerge, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ProjectCombobox } from "@/components/ui/project-combobox";
import { TeamCombobox } from "@/components/ui/team-combobox";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiError, apiFetch } from "@/lib/api";
import { useT, type Translator } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { memoryDetectorState } from "@/lib/memory/honesty";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { useErrorText } from "@/lib/use-error-text";

// --------------------------------------------------------------------------
// Types
// --------------------------------------------------------------------------
type MemoryScope = "private" | "team_shared" | "project_shared" | "global";
type MemoryType = "episodic" | "semantic";

/** Las claves del namespace `memories`, para tipar los mapas de etiquetas. */
type MemoriesKey = Parameters<Translator<"memories">>[0];

interface MemoryResponse {
  id: string;
  tenant_id: string;
  scope: MemoryScope;
  type: MemoryType;
  content: string;
  tags: string[];
  user_id: string | null;
  team_id: string | null;
  project_id: string | null;
  source_execution_id: string | null;
  agent_id: string | null;
  has_embedding: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * Clave del diccionario por scope, no el texto ya resuelto.
 *
 * El mapa sigue existiendo (en vez de disolverse en un `t("scope" + x)`)
 * porque es lo que hace que TypeScript siga exigiendo una entrada por cada
 * valor del enum: con la concatenacion, anadir un scope en el backend
 * compilaria y pintaria la clave cruda en pantalla.
 */
const SCOPE_KEY: Record<MemoryScope | "all", MemoriesKey> = {
  all: "scopeAll",
  private: "scopePrivate",
  team_shared: "scopeTeam",
  project_shared: "scopeProject",
  global: "scopeGlobal",
};

// Colores por scope (de menos a más alcance):
//   private → muted (uso individual)
//   team_shared → info (azul, compartido con team)
//   project_shared → primary (color de marca, contexto del proyecto)
//   global → warning (naranja, "tocas esto y afecta a TODOS")
const SCOPE_VARIANT: Record<MemoryScope, BadgeVariant> = {
  private: "muted",
  team_shared: "info",
  project_shared: "primary",
  global: "warning",
};

const TYPE_KEY: Record<MemoryType, MemoriesKey> = {
  episodic: "typeEpisodic",
  semantic: "typeSemantic",
};

// Episódica = hecho concreto del pasado · Semántica = conocimiento durable.
const TYPE_VARIANT: Record<MemoryType, BadgeVariant> = {
  episodic: "default",
  semantic: "info",
};

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function MemoriesPage() {
  const t = useT("memories");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [scopeFilter, setScopeFilter] = useState<MemoryScope | "all">("all");

  const query = useQuery({
    queryKey: ["memories", scopeFilter],
    queryFn: () =>
      apiFetch<MemoryResponse[]>(
        scopeFilter === "all" ? "/memories" : `/memories?scope=${scopeFilter}`,
      ),
    refetchOnWindowFocus: false,
  });

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8" data-testid="memories-page">
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="memories-header"
      />

      <CreateMemoryCard
        onCreated={() => queryClient.invalidateQueries({ queryKey: ["memories"] })}
      />

      <Card className="mt-6">
        <CardHeader className="flex flex-row items-center justify-between gap-4">
          <CardTitle>
            {t(SCOPE_KEY[scopeFilter])} ({query.data?.length ?? "…"})
          </CardTitle>
          <ScopeFilter value={scopeFilter} onChange={setScopeFilter} />
        </CardHeader>
        <CardContent>
          {query.isLoading ? (
            <p className="text-muted-foreground text-sm">{tCommon("loading")}</p>
          ) : query.isError ? (
            <p className="text-destructive text-sm" data-testid="memories-error">
              {errorText(query.error)}
            </p>
          ) : (query.data ?? []).length === 0 ? (
            <p className="text-muted-foreground text-sm italic" data-testid="memories-empty">
              {t("empty")}
            </p>
          ) : (
            <MemoryList
              memories={query.data ?? []}
              onDeleted={() => queryClient.invalidateQueries({ queryKey: ["memories"] })}
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------
// Scope filter (segmented control)
// --------------------------------------------------------------------------
function ScopeFilter({
  value,
  onChange,
}: {
  value: MemoryScope | "all";
  onChange: (next: MemoryScope | "all") => void;
}) {
  const t = useT("memories");
  const options: (MemoryScope | "all")[] = [
    "all",
    "private",
    "team_shared",
    "project_shared",
    "global",
  ];
  return (
    <div
      className="border-muted bg-muted/40 inline-flex rounded-md border p-0.5 text-xs"
      data-testid="memories-scope-filter"
    >
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          data-testid={`memories-scope-${opt}`}
          aria-pressed={value === opt}
          className={
            "rounded px-2 py-1 transition-colors " +
            (value === opt
              ? "bg-background shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground")
          }
        >
          {t(SCOPE_KEY[opt])}
        </button>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------
// List
// --------------------------------------------------------------------------
function MemoryList({
  memories,
  onDeleted,
}: {
  memories: MemoryResponse[];
  onDeleted: () => void;
}) {
  return (
    <ul className="space-y-2" data-testid="memories-list">
      {memories.map((m) => (
        <MemoryRow key={m.id} memory={m} onDeleted={onDeleted} />
      ))}
    </ul>
  );
}

function MemoryRow({ memory, onDeleted }: { memory: MemoryResponse; onDeleted: () => void }) {
  const t = useT("memories");
  const [similarOpen, setSimilarOpen] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => apiFetch<void>(`/memories/${memory.id}`, { method: "DELETE" }),
    onSuccess: () => onDeleted(),
  });

  return (
    <li
      className="border-muted flex items-start justify-between gap-3 rounded border px-3 py-2 text-sm"
      data-testid={`memory-${memory.id}`}
      data-scope={memory.scope}
      data-type={memory.type}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1 text-[10px] uppercase tracking-wide">
          <Badge variant={SCOPE_VARIANT[memory.scope] ?? "muted"}>
            {t(SCOPE_KEY[memory.scope])}
          </Badge>
          <Badge variant={TYPE_VARIANT[memory.type] ?? "muted"}>{t(TYPE_KEY[memory.type])}</Badge>
          {memory.has_embedding ? (
            <Badge variant="success">embedding</Badge>
          ) : (
            <SimilarUnavailableBadge memoryId={memory.id} />
          )}
          <SimilarCountBadge
            memoryId={memory.id}
            hasEmbedding={memory.has_embedding}
            onClick={() => setSimilarOpen(true)}
          />
          {memory.tags.length > 0 ? (
            <span className="text-muted-foreground ml-1 font-mono">{memory.tags.join(" · ")}</span>
          ) : null}
        </div>
        <div className="mt-1 text-sm">{renderPlanDraft(memory.content)}</div>
      </div>
      <div className="flex gap-1">
        <Button
          variant="outline"
          size="sm"
          onClick={() => deleteMutation.mutate()}
          disabled={deleteMutation.isPending}
          data-testid={`memory-delete-${memory.id}`}
          aria-label={t("delete")}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
      {similarOpen && (
        <SimilarMemoriesDialog
          memory={memory}
          onOpenChange={setSimilarOpen}
          onChanged={onDeleted}
        />
      )}
    </li>
  );
}

// --------------------------------------------------------------------------
// Similar count badge — pre-fetches /similar para cada memoria con
// embedding y muestra "N similar/es" (amarillo, clickable) sólo si
// N > 0. Si no hay candidatos sobre el umbral, no se renderiza nada.
//
// Coste: una query /similar por memoria con embedding. Para tenants
// con muchas memorias conviene un endpoint batched
// `/memories/similar-counts` (follow-up); para volúmenes normales el
// caché de TanStack (staleTime 30s) lo hace barato.
// --------------------------------------------------------------------------

// --------------------------------------------------------------------------
// Honestidad de estado (Plan 06.17 task_06_17_06): una memoria SIN embedding
// no puede tener "similares" (cos-sim sobre un vector ausente) ni participar
// del umbral. En vez de no mostrar nada (que parecía "0 similares, todo bien"),
// se marca "No disponible aún" con el motivo en el tooltip accesible.
// --------------------------------------------------------------------------
function SimilarUnavailableBadge({ memoryId }: { memoryId: string }) {
  const { lang } = useLang();
  const state = memoryDetectorState(false, lang);
  return (
    <Tooltip content={state.note}>
      <TooltipTrigger>
        <Badge variant="muted" data-testid={`memory-similar-unavailable-${memoryId}`}>
          {state.label}
        </Badge>
      </TooltipTrigger>
    </Tooltip>
  );
}

function SimilarCountBadge({
  memoryId,
  hasEmbedding,
  onClick,
}: {
  memoryId: string;
  hasEmbedding: boolean;
  onClick: () => void;
}) {
  const t = useT("memories");
  const query = useQuery<SimilarItem[], ApiError>({
    queryKey: ["memory-similar", memoryId],
    queryFn: () => apiFetch<SimilarItem[]>(`/memories/${memoryId}/similar`),
    enabled: hasEmbedding,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    retry: false,
  });
  const count = query.data?.length ?? 0;
  if (!hasEmbedding || count === 0) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={`memory-similar-badge-${memoryId}`}
      className="bg-warning-soft text-warning-soft-foreground hover:bg-warning-soft/80 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide transition-colors"
      aria-label={t("similarAria", { count })}
    >
      {count === 1 ? t("similarBadgeOne") : t("similarBadgeMany", { count })}
    </button>
  );
}

// --------------------------------------------------------------------------
// Similar memories dialog (Plan 06.7 task_06_7_08)
// --------------------------------------------------------------------------

interface SimilarItem {
  memory: MemoryResponse;
  similarity: number;
}

function SimilarMemoriesDialog({
  memory,
  onOpenChange,
  onChanged,
}: {
  memory: MemoryResponse;
  onOpenChange: (v: boolean) => void;
  onChanged: () => void;
}) {
  const t = useT("memories");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const similarQuery = useQuery<SimilarItem[], ApiError>({
    queryKey: ["memory-similar", memory.id],
    queryFn: () => apiFetch<SimilarItem[]>(`/memories/${memory.id}/similar`),
    refetchOnWindowFocus: false,
  });

  const refetchAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["memory-similar", memory.id] });
    void queryClient.invalidateQueries({ queryKey: ["memories"] });
    onChanged();
  };

  const mergeMutation = useMutation<MemoryResponse, ApiError, string>({
    mutationFn: (sourceId: string) =>
      apiFetch<MemoryResponse>(`/memories/${sourceId}/merge-into`, {
        method: "POST",
        body: { target_id: memory.id },
      }),
    onSuccess: () => refetchAll(),
  });

  const discardMutation = useMutation<void, ApiError, string>({
    mutationFn: (candidateId: string) =>
      apiFetch<void>(`/memories/${candidateId}`, { method: "DELETE" }),
    onSuccess: () => refetchAll(),
  });

  return (
    <Dialog open={true} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("similarTitle")}</DialogTitle>
          <DialogDescription>{t("similarDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <Card className="bg-muted/40 p-3">
            <p className="text-muted-foreground mb-1 text-[10px] uppercase tracking-wide">
              {t("similarTarget")}
            </p>
            <div className="text-sm">{renderPlanDraft(memory.content)}</div>
          </Card>

          {similarQuery.isLoading && (
            <p className="text-muted-foreground text-sm" data-testid="similar-loading">
              {t("similarLoading")}
            </p>
          )}

          {similarQuery.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="similar-error"
            >
              {errorText(similarQuery.error)}
            </p>
          )}

          {similarQuery.data && similarQuery.data.length === 0 && (
            <p className="text-muted-foreground text-sm" data-testid="similar-empty">
              {t("similarEmpty")}
            </p>
          )}

          {similarQuery.data && similarQuery.data.length > 0 && (
            <ul className="space-y-2" data-testid="similar-list">
              {similarQuery.data.map((item) => (
                <li
                  key={item.memory.id}
                  className="border-muted rounded border p-3"
                  data-testid={`similar-item-${item.memory.id}`}
                >
                  <div className="mb-1 flex items-center justify-between">
                    <Badge variant="info" data-testid={`similar-pct-${item.memory.id}`}>
                      {t("similarPercent", { pct: (item.similarity * 100).toFixed(1) })}
                    </Badge>
                    <div className="flex gap-1">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={mergeMutation.isPending}
                        onClick={() => mergeMutation.mutate(item.memory.id)}
                        data-testid={`similar-merge-${item.memory.id}`}
                      >
                        <GitMerge className="mr-1 h-3.5 w-3.5" />
                        {t("merge")}
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={discardMutation.isPending}
                        onClick={() => discardMutation.mutate(item.memory.id)}
                        data-testid={`similar-discard-${item.memory.id}`}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        {t("discard")}
                      </Button>
                    </div>
                  </div>
                  <div className="text-sm">{renderPlanDraft(item.memory.content)}</div>
                </li>
              ))}
            </ul>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------------------------
// Create form
// --------------------------------------------------------------------------
function CreateMemoryCard({ onCreated }: { onCreated: () => void }) {
  const t = useT("memories");
  const errorText = useErrorText();
  const [content, setContent] = useState("");
  const [scope, setScope] = useState<MemoryScope>("team_shared");
  const [type, setType] = useState<MemoryType>("semantic");
  const [teamId, setTeamId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [tagsRaw, setTagsRaw] = useState("");

  const parsedTags = useMemo(
    () =>
      tagsRaw
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t.length > 0),
    [tagsRaw],
  );

  const mutation = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        content,
        scope,
        type,
        tags: parsedTags,
      };
      if (scope === "team_shared") body.team_id = teamId;
      if (scope === "project_shared") body.project_id = projectId;
      return apiFetch<MemoryResponse>("/memories", { method: "POST", body });
    },
    onSuccess: () => {
      setContent("");
      setTagsRaw("");
      onCreated();
    },
  });

  const canSubmit =
    content.trim().length > 0 &&
    !mutation.isPending &&
    (scope !== "team_shared" || teamId.trim().length > 0) &&
    (scope !== "project_shared" || projectId.trim().length > 0);

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle>{t("createTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3"
          data-testid="memory-create-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (canSubmit) mutation.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label>{t("fieldContent")}</Label>
            <MarkdownTextarea
              value={content}
              onChange={setContent}
              rows={5}
              data-testid="memory-content-input"
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="memory-scope-select">{t("fieldScope")}</Label>
              <select
                id="memory-scope-select"
                value={scope}
                onChange={(e) => setScope(e.target.value as MemoryScope)}
                data-testid="memory-scope-select"
                className="bg-background border-muted w-full rounded border px-2 py-1 text-sm"
              >
                <option value="private">{t("scopePrivate")}</option>
                <option value="team_shared">{t("scopeTeam")}</option>
                <option value="project_shared">{t("scopeProject")}</option>
                <option value="global">{t("scopeGlobal")}</option>
              </select>
            </div>
            <div>
              <Label htmlFor="memory-type-select">{t("fieldType")}</Label>
              <select
                id="memory-type-select"
                value={type}
                onChange={(e) => setType(e.target.value as MemoryType)}
                data-testid="memory-type-select"
                className="bg-background border-muted w-full rounded border px-2 py-1 text-sm"
              >
                <option value="semantic">{t("typeSemantic")}</option>
                <option value="episodic">{t("typeEpisodic")}</option>
              </select>
            </div>
          </div>

          {scope === "team_shared" ? (
            <div className="flex flex-col gap-1.5">
              <Label>{t("fieldTeam")}</Label>
              <TeamCombobox
                value={teamId || null}
                onChange={(id) => setTeamId(id ?? "")}
                data-testid="memory-team-id-input"
              />
            </div>
          ) : null}

          {scope === "project_shared" ? (
            <div className="flex flex-col gap-1.5">
              <Label>{t("fieldProject")}</Label>
              <ProjectCombobox
                value={projectId || null}
                onChange={(id) => setProjectId(id ?? "")}
                data-testid="memory-project-id-input"
              />
            </div>
          ) : null}

          <div>
            <Label htmlFor="memory-tags-input">{t("fieldTags")}</Label>
            <Input
              id="memory-tags-input"
              data-testid="memory-tags-input"
              value={tagsRaw}
              onChange={(e) => setTagsRaw(e.target.value)}
              placeholder={t("tagsPlaceholder")}
            />
          </div>

          {mutation.isError ? (
            <p className="text-destructive text-xs" data-testid="memory-create-error">
              {errorText(mutation.error)}
            </p>
          ) : null}

          <div className="flex justify-end">
            <Button type="submit" disabled={!canSubmit} data-testid="memory-create-submit">
              {t("submit")}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
