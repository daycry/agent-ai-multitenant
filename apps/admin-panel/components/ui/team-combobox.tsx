"use client";

/**
 * TeamCombobox — wrapper especializado de EntityCombobox para
 * `GET /teams?q=…&limit=…`. Mapea TeamResponse → EntityOption
 * (id + name; el secundario muestra el nº de miembros para que el
 * operador distinga teams homónimos).
 */

import { Users } from "lucide-react";

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

function toOption(raw: unknown): EntityOption {
  const t = raw as TeamRaw;
  const memberCount = Array.isArray(t.members) ? t.members.length : 0;
  return {
    id: t.id,
    label: t.name,
    secondary: memberCount > 0 ? `${memberCount} miembros` : undefined,
  };
}

export function TeamCombobox({
  value,
  onChange,
  initialLabel,
  placeholder = "Busca un equipo por nombre…",
  disabled,
  className,
  ...props
}: TeamComboboxProps) {
  return (
    <EntityCombobox
      endpoint="/teams"
      toOption={toOption}
      value={value}
      onChange={(id, opt) => onChange(id, opt?.label)}
      initialLabel={initialLabel}
      placeholder={placeholder}
      searchPlaceholder="Buscar por nombre…"
      icon={Users}
      disabled={disabled}
      className={className}
      data-testid={props["data-testid"] ?? "team-combobox"}
    />
  );
}
