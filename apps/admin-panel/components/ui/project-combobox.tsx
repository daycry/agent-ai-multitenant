"use client";

/**
 * ProjectCombobox — wrapper especializado de EntityCombobox para
 * `GET /projects?q=…&limit=…`. Mapea ProjectResponse → EntityOption
 * (id + name + status).
 */

import { FolderKanban } from "lucide-react";

import { EntityCombobox, type EntityOption } from "./entity-combobox";

interface ProjectRaw {
  id: string;
  name: string;
  status: string;
}

interface ProjectComboboxProps {
  value: string | null;
  onChange: (projectId: string | null, name?: string) => void;
  initialLabel?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "data-testid"?: string;
}

function toOption(raw: unknown): EntityOption {
  const p = raw as ProjectRaw;
  return { id: p.id, label: p.name, secondary: p.status };
}

export function ProjectCombobox({
  value,
  onChange,
  initialLabel,
  placeholder = "Busca un proyecto por nombre…",
  disabled,
  className,
  ...props
}: ProjectComboboxProps) {
  return (
    <EntityCombobox
      endpoint="/projects"
      toOption={toOption}
      value={value}
      onChange={(id, opt) => onChange(id, opt?.label)}
      initialLabel={initialLabel}
      placeholder={placeholder}
      searchPlaceholder="Buscar por nombre…"
      icon={FolderKanban}
      disabled={disabled}
      className={className}
      data-testid={props["data-testid"] ?? "project-combobox"}
    />
  );
}
