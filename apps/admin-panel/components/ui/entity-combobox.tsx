"use client";

/**
 * EntityCombobox — buscador genérico con dropdown server-side.
 *
 * Sirve para cualquier recurso del backend que ofrezca `?q=<text>&limit=<n>`
 * sobre un endpoint de listado. El usuario teclea, el componente
 * debouncea 250ms y dispara una query a TanStack Query cacheada por
 * (endpoint, query string).
 *
 * Cada caller especializa el componente vía un wrapper fino (ver
 * `ProjectCombobox`, `TeamCombobox`) que fija el endpoint, el icono
 * y los placeholders. El componente genérico no asume nada sobre el
 * dominio del recurso.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, X, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

export interface EntityOption {
  id: string;
  /** Human-readable label shown in the dropdown + closed state. */
  label: string;
  /** Optional secondary text (status / scope / etc.) shown muted at the right of the row. */
  secondary?: string;
}

interface EntityComboboxProps {
  /** Endpoint base (e.g. `/projects` or `/teams`). The component
   *  appends `?q=<text>&limit=20`. */
  endpoint: string;
  /** Maps the API response into the (id, label, secondary) shape the
   *  dropdown renders. */
  toOption: (raw: unknown) => EntityOption;
  value: string | null;
  onChange: (id: string | null, option?: EntityOption) => void;
  /** Initial label when value is set but the list hasn't been fetched yet. */
  initialLabel?: string;
  /**
   * Texto del estado cerrado. Sin valor cae al del diccionario.
   *
   * Antes era un **valor por defecto de parametro** en castellano fijo
   * (`placeholder = "Selecciona…"`). Un default no puede llamar a un hook,
   * asi que el idioma se resuelve dentro del componente: por eso la prop es
   * opcional y el fallback vive en el cuerpo, no en la firma.
   */
  placeholder?: string;
  searchPlaceholder?: string;
  icon?: LucideIcon;
  disabled?: boolean;
  className?: string;
  "data-testid"?: string;
}

const DEBOUNCE_MS = 250;

export function EntityCombobox({
  endpoint,
  toOption,
  value,
  onChange,
  initialLabel,
  placeholder,
  searchPlaceholder,
  icon: Icon,
  disabled,
  className,
  ...props
}: EntityComboboxProps) {
  const t = useT("combobox");
  const tCommon = useT("common");
  const testid = props["data-testid"] ?? "entity-combobox";
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [selectedLabel, setSelectedLabel] = useState<string | null>(initialLabel ?? null);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handle = setTimeout(() => setDebounced(query.trim()), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const searchQuery = useQuery({
    queryKey: ["entity-combobox", endpoint, debounced],
    queryFn: async () => {
      const qs = new URLSearchParams({ limit: "20" });
      if (debounced) qs.set("q", debounced);
      const data = await apiFetch<unknown[]>(`${endpoint}?${qs.toString()}`);
      return data.map(toOption);
    },
    enabled: open,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  });

  function selectOption(opt: EntityOption) {
    setSelectedLabel(opt.label);
    onChange(opt.id, opt);
    setOpen(false);
    setQuery("");
  }

  function clearSelection() {
    setSelectedLabel(null);
    onChange(null);
    setQuery("");
  }

  const displayLabel = value ? (selectedLabel ?? value.slice(0, 8)) : "";

  return (
    <div ref={wrapRef} className={cn("relative", className)} data-testid={testid}>
      <button
        type="button"
        onClick={() => !disabled && setOpen(true)}
        disabled={disabled}
        data-testid={`${testid}-trigger`}
        className={cn(
          "border-input bg-background hover:bg-muted/30 flex w-full items-center justify-between rounded-md border px-3 py-2 text-sm transition-colors",
          "focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2",
          disabled && "cursor-not-allowed opacity-60",
        )}
      >
        <span className="inline-flex items-center gap-2 truncate">
          {value ? (
            <>
              {Icon && <Icon className="text-muted-foreground h-4 w-4 shrink-0" />}
              <span className="truncate">{displayLabel}</span>
            </>
          ) : (
            <span className="text-muted-foreground">{placeholder ?? t("select")}</span>
          )}
        </span>
        <span className="flex items-center gap-1">
          {value && !disabled && (
            <span
              role="button"
              aria-label={t("clear")}
              onClick={(e) => {
                e.stopPropagation();
                clearSelection();
              }}
              className="text-muted-foreground hover:text-foreground rounded p-0.5"
              data-testid={`${testid}-clear`}
            >
              <X className="h-3.5 w-3.5" />
            </span>
          )}
          <ChevronDown className="text-muted-foreground h-4 w-4" />
        </span>
      </button>

      {open && (
        <div
          className="bg-background absolute z-50 mt-1 w-full rounded-md border shadow-lg"
          data-testid={`${testid}-dropdown`}
        >
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder ?? t("search")}
            className="bg-background w-full border-b px-3 py-2 text-sm focus:outline-none"
            data-testid={`${testid}-search`}
          />
          <div className="max-h-60 overflow-y-auto">
            {searchQuery.isLoading && (
              <p className="text-muted-foreground p-3 text-xs italic">{tCommon("loading")}</p>
            )}
            {searchQuery.isError && (
              <p className="text-destructive p-3 text-xs" data-testid={`${testid}-error`}>
                {t("searchError")}
              </p>
            )}
            {!searchQuery.isLoading &&
              !searchQuery.isError &&
              (searchQuery.data ?? []).length === 0 && (
                <p
                  className="text-muted-foreground p-3 text-xs italic"
                  data-testid={`${testid}-empty`}
                >
                  {debounced ? t("noMatch", { query: debounced }) : t("empty")}
                </p>
              )}
            <ul className="py-1">
              {(searchQuery.data ?? []).map((opt) => (
                <li key={opt.id}>
                  <button
                    type="button"
                    onClick={() => selectOption(opt)}
                    className={cn(
                      "hover:bg-muted/60 flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm",
                      value === opt.id && "bg-muted/40 font-medium",
                    )}
                    data-testid={`${testid}-option-${opt.id}`}
                  >
                    <span className="inline-flex items-center gap-2 truncate">
                      {Icon && <Icon className="text-muted-foreground h-3.5 w-3.5 shrink-0" />}
                      <span className="truncate">{opt.label}</span>
                    </span>
                    {opt.secondary && (
                      <span className="text-muted-foreground text-[10px]">{opt.secondary}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
