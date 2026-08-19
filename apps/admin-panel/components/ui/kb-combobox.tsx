"use client";

/**
 * KbCombobox — wrapper especializado de EntityCombobox para
 * `GET /knowledge-bases?q=…&limit=…` (Plan 06.9 task_06_9_09).
 *
 * Mapea KnowledgeBaseResponse → EntityOption (id + `[categoría] nombre`
 * + embedding model como descriptor secundario). El consumidor recibe
 * el id de la KB seleccionada por `onChange(kbId, name?)`.
 *
 * Plan 06.10 `task_06_10_09`: la etiqueta lleva delante el SLUG de la
 * categoría. Los nombres de KB colisionan entre categorías con toda
 * naturalidad ("Manual" del stack y "Manual" de rol), y el secundario
 * que había —el modelo de embedding— es el mismo para todas desde el
 * ADR 0155, así que no desempata. El embed `category` ya venía en cada
 * item del listado (`to_kb_response`, una query batch para todas las
 * categorías); lo único que faltaba era leerlo.
 *
 * Se usa en:
 *   - Grant dialog del agent detail (task_06_9_10).
 *   - Futura UI de "asignar KB a proyecto" (Plan 04 / wizard).
 *
 * El backend ya tiene el filtro `?q=` añadido en task_06_9_09 para
 * soportar este typeahead sin traer todos los KBs al cliente.
 */

import { Library } from "lucide-react";

import { EntityCombobox, type EntityOption } from "./entity-combobox";

/** Embed `KbCategorySummary` de la respuesta del backend. */
interface KbCategoryRaw {
  slug?: string | null;
  name?: string | null;
}

interface KbRaw {
  id: string;
  name: string;
  embedding_model_id: string;
  /** `null` cuando la KB no está categorizada o su categoría fue borrada. */
  category?: KbCategoryRaw | null;
}

/**
 * `[<slug>] <nombre de la KB>`, o el nombre a secas si no hay categoría.
 *
 * Sin categoría NO se pinta `[]` ni un «sin categoría» inventado: un
 * corchete vacío es ruido, y una etiqueta que el backend no manda sería
 * una cadena traducible escondida dentro de un componente.
 */
export function kbOptionLabel(raw: unknown): string {
  const k = raw as KbRaw;
  const slug = k.category?.slug?.trim();
  return slug ? `[${slug}] ${k.name}` : k.name;
}

interface KbComboboxProps {
  value: string | null;
  onChange: (kbId: string | null, name?: string) => void;
  initialLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "data-testid"?: string;
}

function toOption(raw: unknown): EntityOption {
  const k = raw as KbRaw;
  return { id: k.id, label: kbOptionLabel(k), secondary: k.embedding_model_id };
}

export function KbCombobox({
  value,
  onChange,
  initialLabel,
  placeholder = "Busca una knowledge base por nombre…",
  disabled,
  className,
  ...props
}: KbComboboxProps) {
  return (
    <EntityCombobox
      endpoint="/knowledge-bases"
      toOption={toOption}
      value={value}
      onChange={(id, opt) => onChange(id, opt?.label)}
      initialLabel={initialLabel}
      placeholder={placeholder}
      searchPlaceholder="Buscar por nombre…"
      icon={Library}
      disabled={disabled}
      className={className}
      data-testid={props["data-testid"] ?? "kb-combobox"}
    />
  );
}
