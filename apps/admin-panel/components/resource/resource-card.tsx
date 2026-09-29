"use client";

import * as React from "react";

import { Card } from "@/components/ui/card";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Tarjeta de recurso: cuatro campos siempre en el mismo sitio y en el mismo
 * orden tras el nombre —procedencia, ámbito, uso, última actividad—. El campo
 * que el endpoint de una pantalla no devuelve NO se pasa y no se pinta: nunca
 * se inventa un valor ni se deja una fila vacía que parezca «cero».
 */
export interface ResourceCardProps {
  /** `data-testid` de la tarjeta (cada pantalla conserva el suyo). */
  testId: string;
  name: React.ReactNode;
  /** Insignia junto al nombre (p. ej. «built-in»). */
  badge?: React.ReactNode;
  provenance?: React.ReactNode;
  scope?: React.ReactNode;
  usage?: React.ReactNode;
  lastActivity?: React.ReactNode;
  description?: React.ReactNode;
  /** Contenido propio de la pantalla (equipos, prompt, canales…). */
  extra?: React.ReactNode;
  /** Acciones de pie (enlaces, editar, adoptar…). */
  actions?: React.ReactNode;
  selected?: boolean;
  onSelect: () => void;
  /** Nombre accesible del botón de selección (por defecto, el nombre visible). */
  selectLabel?: string;
  className?: string;
}

export function ResourceCard({
  testId,
  name,
  badge,
  provenance,
  scope,
  usage,
  lastActivity,
  description,
  extra,
  actions,
  selected = false,
  onSelect,
  selectLabel,
  className,
}: ResourceCardProps) {
  const t = useT("resource");
  const fields: [string, string, React.ReactNode][] = [
    ["provenance", t("fieldProvenance"), provenance],
    ["scope", t("fieldScope"), scope],
    ["usage", t("fieldUsage"), usage],
    ["lastActivity", t("fieldLastActivity"), lastActivity],
  ];
  const present = fields.filter(([, , v]) => v !== undefined && v !== null && v !== false);
  return (
    <Card
      data-testid={testId}
      data-selected={selected ? "true" : "false"}
      className={cn(
        "flex h-full flex-col gap-2 p-4",
        selected && "border-primary ring-primary/30 ring-1",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          aria-label={selectLabel}
          data-testid={`resource-select-${testId}`}
          className="min-w-0 text-left text-base font-semibold hover:underline"
        >
          {name}
        </button>
        {badge}
      </div>
      {present.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          {present.map(([key, label, value]) => (
            <React.Fragment key={key}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd data-testid={`resource-field-${key}`}>{value}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}
      {description && <div className="text-muted-foreground text-sm">{description}</div>}
      {extra}
      {actions && <div className="mt-auto flex flex-wrap gap-2 pt-2">{actions}</div>}
    </Card>
  );
}
