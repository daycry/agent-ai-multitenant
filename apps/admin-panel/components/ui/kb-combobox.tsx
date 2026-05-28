"use client";

/**
 * KbCombobox — wrapper especializado de EntityCombobox para
 * `GET /knowledge-bases?q=…&limit=…` (Plan 06.9 task_06_9_09).
 *
 * Mapea KnowledgeBaseResponse → EntityOption (id + name + embedding
 * model como descriptor secundario). El consumidor recibe el id de la
 * KB seleccionada por `onChange(kbId, name?)`.
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

interface KbRaw {
  id: string;
  name: string;
  embedding_model_id: string;
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
  return { id: k.id, label: k.name, secondary: k.embedding_model_id };
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
