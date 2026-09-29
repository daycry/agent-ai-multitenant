"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ListToolbar } from "@/components/shared/list-toolbar";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/** Cabecera: título, contador y acción principal. */
export function ResourceHeader({
  icon,
  title,
  description,
  count,
  countLabel,
  action,
}: {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  count?: number;
  /** Texto ya traducido del contador («3 agentes»). */
  countLabel?: string;
  action?: React.ReactNode;
}) {
  return (
    <PageHeader
      icon={icon}
      title={
        <>
          {title}
          {count !== undefined && (
            <span
              className="text-muted-foreground ml-3 align-middle text-base font-normal"
              data-testid="resource-count"
            >
              {countLabel ?? String(count)}
            </span>
          )}
        </>
      }
      description={description}
      actions={action}
    />
  );
}

/** Barra de búsqueda + filtros (los filtros de cada pantalla van como hijos). */
export function ResourceToolbar({
  search,
  onSearchChange,
  searchPlaceholder,
  searchTestId,
  searchAriaLabel,
  children,
}: {
  search: string;
  onSearchChange: (v: string) => void;
  searchPlaceholder?: string;
  searchTestId?: string;
  searchAriaLabel?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-col gap-3" data-testid="resource-toolbar">
      <ListToolbar
        search={search}
        onSearchChange={onSearchChange}
        searchPlaceholder={searchPlaceholder}
        searchTestId={searchTestId}
        searchAriaLabel={searchAriaLabel}
      />
      {children && <div className="flex flex-wrap items-center gap-4">{children}</div>}
    </div>
  );
}

export interface ResourceField {
  key: string;
  label: string;
  value: React.ReactNode;
}

/** Panel lateral: resumen del recurso seleccionado + enlace a su ficha. */
export function ResourceDetailPanel({
  title,
  fields,
  detailHref,
  onClose,
  children,
}: {
  title: React.ReactNode;
  fields: ResourceField[];
  detailHref?: string;
  onClose: () => void;
  children?: React.ReactNode;
}) {
  const t = useT("resource");
  return (
    <aside
      className="bg-card sticky top-4 flex h-fit flex-col gap-3 rounded-lg border p-4"
      data-testid="resource-panel"
      aria-label={t("panelLabel")}
    >
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-base font-semibold">{title}</h2>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 px-0"
          onClick={onClose}
          aria-label={t("close")}
          data-testid="resource-panel-close"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      {fields.length > 0 && (
        <dl className="flex flex-col gap-2 text-sm">
          {fields.map((f) => (
            <div key={f.key} data-testid={`resource-panel-field-${f.key}`}>
              <dt className="text-muted-foreground text-xs">{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {children}
      {detailHref && (
        <Button variant="outline" size="sm" asChild>
          <Link href={detailHref} data-testid="resource-panel-detail-link">
            {t("openFile")} <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Link>
        </Button>
      )}
    </aside>
  );
}

/** Rejilla de lista + columna del panel (sólo si hay recurso seleccionado). */
export function ResourceLayout({
  list,
  panel,
}: {
  list: React.ReactNode;
  panel?: React.ReactNode;
}) {
  return (
    <div
      className={panel ? "grid grid-cols-1 gap-6 lg:grid-cols-[1fr_20rem]" : undefined}
      data-testid="resource-layout"
    >
      <div className="min-w-0">{list}</div>
      {panel}
    </div>
  );
}
