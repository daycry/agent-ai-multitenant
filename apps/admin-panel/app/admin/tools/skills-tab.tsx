"use client";

/**
 * Pestaña «Skills» de `/admin/tools` (`task_ui_30`): el catálogo de skills con el
 * patrón de recurso. Sólo pinta lo que `GET /skills` devuelve: procedencia
 * (`is_builtin`), categoría y tools requeridas. `/skills` no da uso por agente ni
 * actividad, así que esos campos no se pintan.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";

import {
  ResourceCard,
  ResourceDetailPanel,
  ResourceHeader,
  ResourceLayout,
  ResourceToolbar,
} from "@/components/resource";
import { EmptyState } from "@/components/ui/empty-state";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Espeja los campos de `api_server.schemas.catalog.SkillResponse` que se usan. */
interface CatalogSkill {
  id: string;
  name: string;
  category: string;
  description: string | null;
  prompt_fragment: string;
  required_tools: string[];
  is_builtin: boolean;
}

function categoryText(cat: string): string {
  return cat.charAt(0).toUpperCase() + cat.slice(1);
}

export function SkillsTab() {
  const t = useT("tools");
  const tr = useT("resource");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const skillsQuery = useQuery<CatalogSkill[], ApiError>({
    queryKey: ["skills-catalog"],
    queryFn: () => apiFetch<CatalogSkill[]>("/skills?limit=500"),
    refetchOnWindowFocus: false,
  });
  const skills = useMemo(() => skillsQuery.data ?? [], [skillsQuery.data]);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      skills.filter(
        (s) =>
          q === "" ||
          s.name.toLowerCase().includes(q) ||
          (s.description ?? "").toLowerCase().includes(q) ||
          s.category.toLowerCase().includes(q),
      ),
    [skills, q],
  );
  const selected = skills.find((s) => s.id === selectedId) ?? null;

  const requiredText = (n: number) =>
    t(n === 1 ? "skillRequiredToolsOne" : "skillRequiredToolsMany", { count: n });

  return (
    <div data-testid="skills-tab">
      <ResourceHeader
        icon={<Sparkles className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("skillsTitle")}
        description={t("skillsDescription")}
        count={skillsQuery.data ? skills.length : undefined}
        countLabel={t(skills.length === 1 ? "countSkillsOne" : "countSkillsMany", {
          count: skills.length,
        })}
      />
      <ResourceToolbar
        search={query}
        onSearchChange={setQuery}
        searchPlaceholder={t("skillsSearchPlaceholder")}
        searchAriaLabel={t("skillsSearchAria")}
        searchTestId="skills-search"
      />
      <StateBlock
        isLoading={skillsQuery.isLoading}
        loadingSkeleton
        skeletonRows={5}
        loadingTestId="skills-loading"
        isError={skillsQuery.isError}
        error={skillsQuery.error}
        errorTitle={t("skillsErrorTitle")}
        errorTestId="skills-error"
      >
        {filtered.length === 0 ? (
          <EmptyState
            icon={Sparkles}
            title={q !== "" ? tr("noMatches") : t("skillsEmpty")}
            data-testid="skills-empty"
          />
        ) : (
          <ResourceLayout
            panel={
              selected ? (
                <ResourceDetailPanel
                  title={selected.name}
                  onClose={() => setSelectedId(null)}
                  fields={[
                    {
                      key: "provenance",
                      label: tr("fieldProvenance"),
                      value: selected.is_builtin ? tr("provBuiltin") : tr("provOwn"),
                    },
                    {
                      key: "category",
                      label: t("skillFieldCategory"),
                      value: categoryText(selected.category),
                    },
                    ...(selected.description
                      ? [
                          {
                            key: "description",
                            label: tr("fieldDescription"),
                            value: selected.description,
                          },
                        ]
                      : []),
                    ...(selected.required_tools.length > 0
                      ? [
                          {
                            key: "usage",
                            label: tr("fieldUsage"),
                            value: requiredText(selected.required_tools.length),
                          },
                        ]
                      : []),
                    {
                      key: "prompt",
                      label: t("skillFieldPrompt"),
                      value: (
                        <span className="whitespace-pre-wrap break-words">
                          {selected.prompt_fragment}
                        </span>
                      ),
                    },
                  ]}
                />
              ) : undefined
            }
            list={
              <ul className="grid gap-3 sm:grid-cols-2" data-testid="skills-list">
                {filtered.map((s) => (
                  <li key={s.id}>
                    <ResourceCard
                      testId={`skill-card-${s.id}`}
                      name={s.name}
                      provenance={s.is_builtin ? tr("provBuiltin") : tr("provOwn")}
                      usage={
                        s.required_tools.length > 0
                          ? requiredText(s.required_tools.length)
                          : undefined
                      }
                      description={s.description ?? undefined}
                      extra={
                        <span className="text-muted-foreground text-xs">
                          {categoryText(s.category)}
                        </span>
                      }
                      selected={selectedId === s.id}
                      onSelect={() => setSelectedId(s.id)}
                      selectLabel={tr("selectAria", { name: s.name })}
                    />
                  </li>
                ))}
              </ul>
            }
          />
        )}
      </StateBlock>
    </div>
  );
}
