"use client";

/**
 * TeamCombobox — wrapper especializado de EntityCombobox para
 * `GET /teams?q=…&limit=…`. Mapea TeamResponse → EntityOption
 * (id + name; el secundario muestra el nº de miembros para que el
 * operador distinga teams homónimos).
 */

import { Users } from "lucide-react";

import { translate, type Lang } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";

import { EntityCombobox, type EntityOption } from "./entity-combobox";

interface TeamRaw {
  id: string;
  name: string;
  members?: unknown[];
}

interface TeamComboboxProps {
  value: string | null;
  onChange: (teamId: string | null, name?: string) => void;
  initialLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "data-testid"?: string;
}

/**
 * El descriptor secundario se traduce con `translate` y no con `useT`.
 *
 * `toOption` la llama `EntityCombobox` dentro de su `queryFn`, no durante el
 * render: un hook ahi seria una llamada condicional. `translate` es la misma
 * funcion pura que hay debajo de `useT`, asi que el texto sale del mismo
 * diccionario; lo unico que cambia es de donde llega el idioma.
 */
function toOption(raw: unknown, lang: Lang): EntityOption {
  const t = raw as TeamRaw;
  const memberCount = Array.isArray(t.members) ? t.members.length : 0;
  return {
    id: t.id,
    label: t.name,
    secondary:
      memberCount > 0 ? translate(lang, "combobox", "teamMembers", { n: memberCount }) : undefined,
  };
}

export function TeamCombobox({
  value,
  onChange,
  initialLabel,
  placeholder,
  disabled,
  className,
  ...props
}: TeamComboboxProps) {
  const lang = useLangOptional();
  return (
    <EntityCombobox
      endpoint="/teams"
      toOption={(raw) => toOption(raw, lang)}
      value={value}
      onChange={(id, opt) => onChange(id, opt?.label)}
      initialLabel={initialLabel}
      placeholder={placeholder ?? translate(lang, "combobox", "teamPlaceholder")}
      searchPlaceholder={translate(lang, "combobox", "searchByName")}
      icon={Users}
      disabled={disabled}
      className={className}
      data-testid={props["data-testid"] ?? "team-combobox"}
    />
  );
}
