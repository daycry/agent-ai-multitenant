"use client";

/**
 * Sub-sección "Skills del agente" del detalle de agente
 * (Plan 06.18 task_06_18_13, ADR 0050 Opción A).
 *
 * Espeja la mecánica tarjeta-fila de `<AgentToolsSection>`: TanStack-Query
 * para leer/escribir, `isReadOnly`, estados vacío/cargando/error, layout
 * shadcn/ui. La selección se guarda declarativamente con
 * `PUT /agents/{id}/skills` (set completo); lista vacía → sin inyección de
 * prompt (comportamiento previo intacto).
 *
 * Las skills inyectan su `prompt_fragment` en el system prompt EFECTIVO del
 * agente en runtime. Verbo único "Asignar/Quitar" (la fila completa togglea
 * vía `label htmlFor`; `cursor-pointer` solo cuando `canEdit`).
 *
 * Read-only para agentes `global_builtin` y para usuarios no `tenant_admin`.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Boxes,
  FlaskConical,
  GraduationCap,
  type LucideIcon,
  Microscope,
  Search,
  Server,
  Ticket,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useCurrentUser } from "@/lib/use-current-user";
import { useErrorText } from "@/lib/use-error-text";

// ---------------------------------------------------------------------------
// Types (mirror api_server.schemas.catalog.SkillResponse +
// api_server.schemas.agents.AgentSkillResponse)
// ---------------------------------------------------------------------------
interface CatalogSkill {
  id: string;
  name: string;
  category: string;
  description: string | null;
  prompt_fragment: string;
  is_builtin: boolean;
}

interface AgentSkillRow {
  skill_id: string;
  name: string;
  category: string;
  description: string | null;
  prompt_fragment: string;
  is_builtin: boolean;
}

interface AgentSkillsSectionProps {
  agentId: string;
  /** Read-only when the agent is `global_builtin` (platform-managed). */
  isReadOnly: boolean;
}

// ---------------------------------------------------------------------------
// Etiquetas humanas — sin enums crudos (categorías del seed, ADR 0050; +atlassian
// como bucket de integración, ADR 0127/0128)
// ---------------------------------------------------------------------------
const CATEGORY_LABEL: Record<string, string> = {
  backend: "Backend",
  frontend: "Frontend",
  devops: "DevOps",
  qa: "QA / Testing",
  research: "Investigación",
  docs: "Documentación",
  atlassian: "Atlassian (Jira/Confluence)",
};

const CATEGORY_ICON: Record<string, LucideIcon> = {
  backend: Server,
  frontend: Boxes,
  devops: Server,
  qa: FlaskConical,
  research: Microscope,
  docs: BookOpen,
  atlassian: Ticket,
};

const CATEGORY_ORDER = ["backend", "frontend", "devops", "qa", "research", "docs", "atlassian"];

function categoryLabel(cat: string): string {
  return CATEGORY_LABEL[cat] ?? cat.charAt(0).toUpperCase() + cat.slice(1);
}

function categoryRank(cat: string): number {
  const i = CATEGORY_ORDER.indexOf(cat);
  return i === -1 ? CATEGORY_ORDER.length : i;
}

export function AgentSkillsSection({ agentId, isReadOnly }: AgentSkillsSectionProps) {
  const errorText = useErrorText();
  const t = useT("agents");
  const queryClient = useQueryClient();
  const { isTenantAdmin, isLoading: roleLoading } = useCurrentUser();

  // Los no-admin (tenant_user) ven solo lectura — el backend rechazaría el PUT
  // con 403 igualmente, así que ocultamos el affordance.
  const canEdit = !isReadOnly && isTenantAdmin;

  const catalogQuery = useQuery({
    queryKey: ["skills-catalog"],
    queryFn: () => apiFetch<CatalogSkill[]>("/skills?limit=500"),
    refetchOnWindowFocus: false,
  });

  const assignedQuery = useQuery({
    queryKey: ["agent-skills", agentId],
    queryFn: () => apiFetch<AgentSkillRow[]>(`/agents/${agentId}/skills`),
    refetchOnWindowFocus: false,
  });

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const assignedIds = useMemo(
    () => (assignedQuery.data ?? []).map((r) => r.skill_id).sort(),
    [assignedQuery.data],
  );

  useEffect(() => {
    if (assignedQuery.data) {
      setSelected(new Set(assignedIds));
      setDirty(false);
    }
  }, [assignedQuery.data, assignedIds]);

  const saveMutation = useMutation({
    mutationFn: (skillIds: string[]) =>
      apiFetch<AgentSkillRow[]>(`/agents/${agentId}/skills`, {
        method: "PUT",
        body: { skills: skillIds.map((skill_id) => ({ skill_id })) },
      }),
    onSuccess: () => {
      setSaveError(null);
      void queryClient.invalidateQueries({ queryKey: ["agent-skills", agentId] });
    },
    onError: (err) => {
      setSaveError(errorText(err));
    },
  });

  const toggle = (skillId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(skillId)) next.delete(skillId);
      else next.add(skillId);
      return next;
    });
    setDirty(true);
  };

  const toggleMany = (skillIds: string[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of skillIds) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
    setDirty(true);
  };

  const reset = () => {
    setSelected(new Set(assignedIds));
    setDirty(false);
    setSaveError(null);
  };

  const isLoading = catalogQuery.isLoading || assignedQuery.isLoading || roleLoading;
  const isError = catalogQuery.isError || assignedQuery.isError;
  const errorMsg =
    (catalogQuery.error instanceof Error && catalogQuery.error.message) ||
    (assignedQuery.error instanceof Error && assignedQuery.error.message) ||
    "error desconocido";

  const catalog = useMemo(() => catalogQuery.data ?? [], [catalogQuery.data]);

  const q = query.trim().toLowerCase();
  const matches = useMemo(
    () =>
      catalog.filter(
        (s) =>
          q === "" ||
          s.name.toLowerCase().includes(q) ||
          (s.description ?? "").toLowerCase().includes(q) ||
          categoryLabel(s.category).toLowerCase().includes(q),
      ),
    [catalog, q],
  );

  return (
    <Card data-testid="agent-skills-section">
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <div className="min-w-0">
          <CardTitle className="text-base">
            <span className="inline-flex items-center gap-2">
              <GraduationCap className="h-4 w-4" /> Skills del agente
            </span>
          </CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">
            Asigna skills para inyectar sus instrucciones en el prompt del agente. Sin ninguna
            asignada, el prompt queda intacto.
            <span className="ml-1 font-medium">{selected.size} asignadas.</span>
          </p>
        </div>
        {canEdit && (
          <div className="flex shrink-0 items-center gap-2">
            {dirty && (
              <Button
                variant="outline"
                size="sm"
                onClick={reset}
                disabled={saveMutation.isPending}
                data-testid="agent-skills-reset"
              >
                Descartar
              </Button>
            )}
            <Button
              size="sm"
              onClick={() => saveMutation.mutate(Array.from(selected))}
              disabled={!dirty || saveMutation.isPending}
              data-testid="agent-skills-save"
            >
              {saveMutation.isPending ? "Guardando…" : "Guardar"}
            </Button>
          </div>
        )}
      </CardHeader>

      <CardContent>
        {isLoading && (
          <div className="flex justify-center p-4" data-testid="agent-skills-loading">
            <Spinner />
          </div>
        )}

        {!isLoading && isError && (
          <p className="text-danger-soft-foreground text-sm" data-testid="agent-skills-error">
            No se pudieron cargar las skills: {errorMsg}.
          </p>
        )}

        {!isLoading && !isError && (
          <>
            {saveError && (
              <p
                className="bg-danger-soft text-danger-soft-foreground mb-3 rounded p-2 text-xs"
                data-testid="agent-skills-save-error"
              >
                {saveError}
              </p>
            )}

            <div className="relative mb-3">
              <Search
                className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden="true"
              />
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("skillsSearchPlaceholder")}
                aria-label={t("skillsSearchLabel")}
                className="pl-9"
                data-testid="agent-skills-search"
              />
            </div>

            <GroupedSkillList
              skills={matches}
              selected={selected}
              canEdit={canEdit}
              onToggle={toggle}
              onToggleMany={toggleMany}
              emptyMessage={
                q
                  ? "Ninguna skill coincide con la búsqueda."
                  : "No hay skills en el catálogo. Crea una en /skills."
              }
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Skills agrupadas por categoría (header humano + "asignar todas" por grupo)
// ---------------------------------------------------------------------------
function GroupedSkillList({
  skills,
  selected,
  canEdit,
  onToggle,
  onToggleMany,
  emptyMessage,
}: {
  skills: CatalogSkill[];
  selected: Set<string>;
  canEdit: boolean;
  onToggle: (skillId: string) => void;
  onToggleMany: (skillIds: string[], on: boolean) => void;
  emptyMessage: string;
}) {
  const groups = useMemo(() => {
    const byCat = new Map<string, CatalogSkill[]>();
    for (const s of skills) {
      const arr = byCat.get(s.category) ?? [];
      arr.push(s);
      byCat.set(s.category, arr);
    }
    return Array.from(byCat.entries())
      .map(([cat, items]) => ({
        cat,
        items: items.sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => categoryRank(a.cat) - categoryRank(b.cat) || a.cat.localeCompare(b.cat));
  }, [skills]);

  if (skills.length === 0) {
    return (
      <p className="text-muted-foreground py-4 text-sm italic" data-testid="agent-skills-empty">
        {emptyMessage}
      </p>
    );
  }

  return (
    <div className="space-y-4" data-testid="agent-skills-list">
      {groups.map(({ cat, items }) => {
        const Icon = CATEGORY_ICON[cat] ?? GraduationCap;
        const ids = items.map((s) => s.id);
        const selectedCount = ids.filter((id) => selected.has(id)).length;
        const allOn = selectedCount === ids.length;
        return (
          <section key={cat} data-testid={`agent-skills-group-${cat}`}>
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <h4 className="text-muted-foreground inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide">
                <Icon className="h-3.5 w-3.5" />
                {categoryLabel(cat)}
                <span className="text-muted-foreground/70 font-normal normal-case">
                  ({selectedCount}/{ids.length})
                </span>
              </h4>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => onToggleMany(ids, !allOn)}
                  className="text-primary text-xs hover:underline"
                  data-testid={`agent-skills-group-toggle-${cat}`}
                >
                  {allOn ? "Quitar todas" : "Asignar todas"}
                </button>
              )}
            </div>
            <ul className="space-y-2">
              {items.map((skill) => (
                <SkillRow
                  key={skill.id}
                  skill={skill}
                  checked={selected.has(skill.id)}
                  canEdit={canEdit}
                  onToggle={onToggle}
                />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function SkillRow({
  skill,
  checked,
  canEdit,
  onToggle,
}: {
  skill: CatalogSkill;
  checked: boolean;
  canEdit: boolean;
  onToggle: (skillId: string) => void;
}) {
  const inputId = `agent-skill-${skill.id}`;
  return (
    <li
      className={[
        "flex items-start gap-3 rounded border p-3 transition-colors",
        checked ? "border-primary/60 bg-primary/5" : "hover:bg-muted/40",
      ].join(" ")}
      data-testid={`agent-skill-row-${skill.id}`}
    >
      <Checkbox
        id={inputId}
        className="mt-0.5"
        checked={checked}
        disabled={!canEdit}
        onChange={() => onToggle(skill.id)}
        data-testid={`agent-skill-checkbox-${skill.id}`}
      />
      <label
        htmlFor={inputId}
        className={["min-w-0 flex-1", canEdit ? "cursor-pointer" : "cursor-default"].join(" ")}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{skill.name}</span>
          <Badge variant={skill.is_builtin ? "info" : "muted"}>
            {skill.is_builtin ? "Catálogo" : "Custom"}
          </Badge>
        </div>
        {skill.description && (
          <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">{skill.description}</p>
        )}
      </label>
    </li>
  );
}
