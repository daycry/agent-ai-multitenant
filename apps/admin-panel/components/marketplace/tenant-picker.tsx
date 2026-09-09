"use client";

/**
 * Selector del tenant destino al compartir un listing (`task_mk_23`, UI-06).
 *
 * Hasta ahora el diálogo pedía el UUID del tenant, que nadie tiene a mano. Este
 * buscador consulta `GET /marketplace/shares/tenant-directory?q=` (tenant_admin,
 * mínimo dos caracteres, sin el propio tenant) y deja elegido `{id, name}`; el
 * `value` que sale sigue siendo el id, que es lo que el `POST /marketplace/shares`
 * espera. Con algo elegido se enseña el nombre y un botón para cambiarlo.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

export interface TenantDirectoryEntry {
  id: string;
  name: string;
  slug: string;
}

const MIN_QUERY = 2;
const DEBOUNCE_MS = 250;

export function TenantPicker({
  value,
  onChange,
  inputId = "share-target",
}: {
  /** El id del tenant elegido; `""` = nada elegido. */
  value: string;
  onChange: (tenantId: string, entry: TenantDirectoryEntry | null) => void;
  inputId?: string;
}) {
  const t = useT("marketplace");
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [selected, setSelected] = useState<TenantDirectoryEntry | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => setDebounced(query.trim()), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  // Si el padre vacía el valor (tras compartir), se vuelve al buscador.
  useEffect(() => {
    if (value === "") setSelected(null);
  }, [value]);

  const enabled = selected === null && debounced.length >= MIN_QUERY;
  const directory = useQuery({
    queryKey: ["marketplace-tenant-directory", debounced],
    queryFn: () =>
      apiFetch<TenantDirectoryEntry[]>(
        `/marketplace/shares/tenant-directory?q=${encodeURIComponent(debounced)}`,
      ),
    enabled,
  });

  function pick(entry: TenantDirectoryEntry) {
    setSelected(entry);
    setQuery("");
    onChange(entry.id, entry);
  }

  function clear() {
    setSelected(null);
    onChange("", null);
  }

  if (selected) {
    return (
      <div
        className="flex items-center justify-between gap-2 rounded border px-3 py-2 text-sm"
        data-testid="share-target-selected"
      >
        <span className="flex min-w-0 items-center gap-2">
          <Building2 aria-hidden="true" className="text-muted-foreground h-4 w-4 shrink-0" />
          <span className="truncate font-medium">{selected.name}</span>
          <span className="text-muted-foreground truncate text-xs">{selected.slug}</span>
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={clear}
          aria-label={t("shareTargetClear")}
          data-testid="share-target-clear"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </Button>
      </div>
    );
  }

  const results = directory.data ?? [];
  return (
    <div className="space-y-2">
      <Input
        id={inputId}
        type="search"
        autoComplete="off"
        placeholder={t("shareTargetSearchPlaceholder")}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        data-testid="share-target-input"
      />
      {debounced.length < MIN_QUERY ? (
        <p className="text-muted-foreground text-xs">{t("shareTargetSearchHint")}</p>
      ) : directory.isLoading ? (
        <p className="text-muted-foreground text-xs">{t("shareTargetSearching")}</p>
      ) : directory.isError ? (
        <p className="text-destructive text-xs" data-testid="share-target-error">
          {t("shareTargetSearchError")}
        </p>
      ) : results.length === 0 ? (
        <p className="text-muted-foreground text-xs" data-testid="share-target-no-matches">
          {t("shareTargetNoMatches")}
        </p>
      ) : (
        <ul className="divide-y rounded border" data-testid="share-target-results">
          {results.map((entry) => (
            <li key={entry.id}>
              <button
                type="button"
                className="hover:bg-muted/60 flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm"
                onClick={() => pick(entry)}
                data-testid={`share-target-option-${entry.slug}`}
              >
                <span className="truncate font-medium">{entry.name}</span>
                <span className="text-muted-foreground shrink-0 text-xs">{entry.slug}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
