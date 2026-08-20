"use client";

/**
 * DocsFiltersPanel — facet filters over the tree + search (Plan 07 task_07_15).
 *
 * Two facets, each a multi-select of toggle chips:
 *
 *   * **Categoría** — the canonical docs folder (`01-overview`, …, `other`).
 *   * **Tipo** — the coarse doc kind (ADR, changelog, runbook, README, doc).
 *
 * Selection is lifted to the page (so the same {@link DocsFilter} prunes the
 * sidebar tree and the search hits). An empty facet means "no constraint". A
 * single "Limpiar" action resets every facet. Purely a controlled component —
 * it owns no state and triggers no fetches.
 *
 * The "by project" facet from the roadmap title is the project selection in the
 * tree itself (everything is already scoped to `?project=`), so it isn't
 * duplicated here; recency lives in the bookmarks view, where docs carry an
 * `addedAt` timestamp (the tree/search API exposes no per-doc mtime).
 */

import { FilterX } from "lucide-react";

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import {
  DOC_CATEGORIES,
  DOC_CATEGORY_KEYS,
  DOC_TYPES,
  DOC_TYPE_KEYS,
  isFilterActive,
  type DocCategory,
  type DocsFilter,
  type DocType,
} from "@/lib/docs-filters";

interface DocsFiltersPanelProps {
  filter: DocsFilter;
  onChange: (next: DocsFilter) => void;
  /** Disabled until a project is selected (nothing to scope to otherwise). */
  disabled?: boolean;
}

function toggleInSet<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export function DocsFiltersPanel({ filter, onChange, disabled = false }: DocsFiltersPanelProps) {
  const t = useT("docs");
  const tFacets = useT("docFacets");
  const active = isFilterActive(filter);

  const toggleCategory = (category: DocCategory) => {
    onChange({ ...filter, categories: toggleInSet(filter.categories, category) });
  };
  const toggleType = (type: DocType) => {
    onChange({ ...filter, types: toggleInSet(filter.types, type) });
  };
  const clear = () => {
    onChange({ categories: new Set<DocCategory>(), types: new Set<DocType>() });
  };

  return (
    <div className="flex flex-col gap-3" data-testid="docs-filters-panel">
      <div className="flex items-center justify-between">
        <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
          {t("filtersHeading")}
        </p>
        {active && (
          <button
            type="button"
            onClick={clear}
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs transition-colors"
            data-testid="docs-filters-clear"
          >
            <FilterX className="h-3.5 w-3.5" aria-hidden="true" />
            {t("filtersClear")}
          </button>
        )}
      </div>

      <FacetGroup
        legend={t("facetCategory")}
        testid="docs-filter-categories"
        disabled={disabled}
        options={DOC_CATEGORIES.map((c) => ({ value: c, label: tFacets(DOC_CATEGORY_KEYS[c]) }))}
        selected={filter.categories}
        onToggle={toggleCategory}
        chipTestid={(value) => `docs-filter-category-${value}`}
      />

      <FacetGroup
        legend={t("facetType")}
        testid="docs-filter-types"
        disabled={disabled}
        options={DOC_TYPES.map((type) => ({ value: type, label: tFacets(DOC_TYPE_KEYS[type]) }))}
        selected={filter.types}
        onToggle={toggleType}
        chipTestid={(value) => `docs-filter-type-${value}`}
      />
    </div>
  );
}

function FacetGroup<T extends string>({
  legend,
  testid,
  disabled,
  options,
  selected,
  onToggle,
  chipTestid,
}: {
  legend: string;
  testid: string;
  disabled: boolean;
  options: { value: T; label: string }[];
  selected: ReadonlySet<T>;
  onToggle: (value: T) => void;
  chipTestid: (value: T) => string;
}) {
  return (
    <fieldset className="flex flex-col gap-1.5" disabled={disabled} data-testid={testid}>
      <legend className="text-muted-foreground mb-1 text-[11px] font-medium">{legend}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map(({ value, label }) => {
          const on = selected.has(value);
          return (
            <button
              key={value}
              type="button"
              onClick={() => onToggle(value)}
              aria-pressed={on}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs transition-colors",
                "disabled:cursor-not-allowed disabled:opacity-50",
                on
                  ? "border-primary/50 bg-primary/10 text-primary font-medium"
                  : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
              )}
              data-testid={chipTestid(value)}
            >
              {label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
