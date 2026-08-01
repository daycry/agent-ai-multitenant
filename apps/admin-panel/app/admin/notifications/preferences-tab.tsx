"use client";

/**
 * Pestaña «Preferencias» — la matriz evento × transporte (prod-16
 * `task_prod16_08`, extracción verbatim del `page.tsx` de 831 líneas).
 *
 * La matriz solo ofrece columna por transporte que el tenant TIENE configurado,
 * y una casilla sin regla guardada sale marcada: es el default del dispatcher
 * (la regla más específica gana, y no haber ninguna significa «manda»).
 */

import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

import {
  EVENT_CATALOG_FALLBACK,
  type EventCatalogEntry,
  type NotificationChannel,
  type NotificationPreference,
  type PreferenceUpsertBody,
} from "./notification-types";

export function PreferencesTab() {
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const prefsQuery = useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => apiFetch<NotificationPreference[]>("/notifications/preferences"),
    refetchOnWindowFocus: false,
  });
  const channelsQuery = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => apiFetch<NotificationChannel[]>("/notifications/channels"),
    refetchOnWindowFocus: false,
  });
  const catalogQuery = useQuery({
    queryKey: ["notification-event-catalog"],
    queryFn: () => apiFetch<EventCatalogEntry[]>("/notifications/event-catalog"),
    refetchOnWindowFocus: false,
  });
  const eventCatalog =
    catalogQuery.data && catalogQuery.data.length > 0 ? catalogQuery.data : EVENT_CATALOG_FALLBACK;

  const upsertMutation = useMutation({
    mutationFn: (body: PreferenceUpsertBody) =>
      apiFetch<NotificationPreference>("/notifications/preferences", { method: "PUT", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notification-preferences"] }),
  });

  // The channel transports the tenant actually has configured — the matrix
  // only offers a column per configured transport.
  const channelTypes = useMemo(() => {
    const set = new Set<string>();
    for (const c of channelsQuery.data ?? []) set.add(c.channel_type);
    return [...set].sort();
  }, [channelsQuery.data]);

  // Effective opt-in/out per (event, channel): a stored rule wins, else
  // default ON (the dispatcher's most-specific-wins default).
  const byKey = useMemo(() => {
    const map = new Map<string, NotificationPreference>();
    for (const p of prefsQuery.data ?? []) {
      map.set(`${p.event_type}::${p.channel_type}`, p);
    }
    return map;
  }, [prefsQuery.data]);

  if (prefsQuery.isLoading || channelsQuery.isLoading) {
    return <p className="text-muted-foreground mt-4 text-sm">Cargando…</p>;
  }
  if (prefsQuery.isError) {
    return (
      <p className="text-destructive mt-4 text-sm" data-testid="preferences-error">
        {errorText(prefsQuery.error)}
      </p>
    );
  }

  return (
    <Card className="mt-4" data-testid="preferences-tab">
      <CardHeader>
        <CardTitle className="text-base">Reglas de enrutado</CardTitle>
      </CardHeader>
      <CardContent>
        {channelTypes.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="preferences-empty">
            Configura al menos un canal para ajustar qué eventos llegan por qué transporte.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="preferences-matrix">
              <thead>
                <tr className="text-muted-foreground text-left">
                  <th className="py-2 pr-4 font-medium">Evento</th>
                  {channelTypes.map((type) => (
                    <th key={type} className="px-3 py-2 font-medium capitalize">
                      {type}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {eventCatalog.map(({ event_type: event, label_es }) => (
                  <tr key={event} className="border-t" data-testid={`preferences-row-${event}`}>
                    <td className="py-2 pr-4">
                      <span className="block text-xs">{label_es}</span>
                      <span className="text-muted-foreground block font-mono text-[10px]">
                        {event}
                      </span>
                    </td>
                    {channelTypes.map((type) => {
                      const rule = byKey.get(`${event}::${type}`);
                      const enabled = rule?.enabled ?? true;
                      return (
                        <td key={type} className="px-3 py-2">
                          <RoleGuard
                            min="tenant_admin"
                            fallback={
                              <span className="text-muted-foreground text-xs">
                                {enabled ? "sí" : "no"}
                              </span>
                            }
                          >
                            <input
                              type="checkbox"
                              className="h-4 w-4 rounded border"
                              checked={enabled}
                              disabled={upsertMutation.isPending}
                              data-testid={`preference-${event}-${type}`}
                              onChange={(e) =>
                                upsertMutation.mutate({
                                  scope: "user",
                                  event_type: event,
                                  channel_type: type,
                                  enabled: e.target.checked,
                                })
                              }
                            />
                          </RoleGuard>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {upsertMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="preferences-save-error">
            {errorText(upsertMutation.error)}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
