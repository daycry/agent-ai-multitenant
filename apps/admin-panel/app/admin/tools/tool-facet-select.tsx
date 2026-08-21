"use client";

/**
 * El `<Select>` de una faceta del catálogo: «Todas» + una opción por término del
 * value-set cerrado, siempre con su etiqueta y NUNCA con el slug crudo.
 *
 * Pieza colocada, sacada de `page.tsx` al trocearlo (prod-16 `task_prod16_08`).
 */

import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { pickLang } from "@/lib/i18n";
import type { Lang } from "@/lib/lang-context";

import { ALL, type BilingualTermLike } from "./tool-types";

/**
 * La etiqueta de un término de la taxonomía en el idioma activo.
 *
 * Va con `pickLang` y NO con el diccionario a propósito: estos labels son la
 * fuente única de ADR 0049 que comparten catálogo, asignación y diagnóstico
 * (`lib/tools/taxonomy.ts`). Duplicarlos como claves reabriría la divergencia
 * que aquel ADR cerró. `pickLang` además cae al otro idioma si una cara viniera
 * vacía, cosa que el ternario que había aquí no hacía.
 */
export function facetLabel(term: BilingualTermLike | undefined, slug: string, lang: Lang): string {
  if (!term) return slug;
  return pickLang(lang, { es: term.labelEs, en: term.labelEn });
}

export function FacetSelect({
  id,
  label,
  allLabel,
  value,
  onChange,
  options,
  terms,
  lang,
  testid,
}: {
  id: string;
  label: string;
  allLabel: string;
  value: string;
  onChange: (next: string) => void;
  options: string[];
  terms: Record<string, BilingualTermLike>;
  lang: Lang;
  testid: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </Label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} data-testid={testid}>
        <option value={ALL}>{allLabel}</option>
        {options.map((slug) => (
          <option key={slug} value={slug}>
            {facetLabel(terms[slug], slug, lang)}
          </option>
        ))}
      </Select>
    </div>
  );
}
