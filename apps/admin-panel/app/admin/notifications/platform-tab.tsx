"use client";

/**
 * Pestaña «Plataforma» — qué transportes están habilitados globalmente
 * (prod-16 `task_prod16_08`, extracción verbatim del `page.tsx` de 831 líneas).
 *
 * Solo System Admin puede guardar; el resto la ve en lectura. La barrera real
 * es el backend (`require_system_admin`), el `RoleGuard` es cortesía.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { type PlatformChannelTypes } from "./notification-types";

export function PlatformTab() {
  const t = useT("notifications");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["notification-platform-types"],
    queryFn: () => apiFetch<PlatformChannelTypes>("/notifications/platform/channel-types"),
    refetchOnWindowFocus: false,
  });

  const [draft, setDraft] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (query.data) setDraft(new Set(query.data.enabled));
  }, [query.data]);

  const saveMutation = useMutation({
    mutationFn: (enabled: string[]) =>
      apiFetch<PlatformChannelTypes>("/notifications/platform/channel-types", {
        method: "PUT",
        body: { enabled },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notification-platform-types"] }),
  });

  if (query.isLoading) {
    return <p className="text-muted-foreground mt-4 text-sm">{tCommon("loading")}</p>;
  }
  if (query.isError || !query.data || draft === null) {
    return (
      <p className="text-destructive mt-4 text-sm" data-testid="platform-error">
        {errorText(query.error)}
      </p>
    );
  }

  function toggle(type: string) {
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  return (
    <Card className="mt-4" data-testid="platform-channel-types">
      <CardHeader>
        <CardTitle className="text-base">{t("platformTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground mb-4 text-sm">{t("platformHint")}</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {query.data.available.map((type) => (
            <label
              key={type}
              className="flex items-center gap-2 text-sm capitalize"
              htmlFor={`platform-type-${type}`}
            >
              <input
                id={`platform-type-${type}`}
                data-testid={`platform-type-${type}`}
                type="checkbox"
                className="h-4 w-4 rounded border"
                checked={draft.has(type)}
                onChange={() => toggle(type)}
              />
              <span>{type}</span>
            </label>
          ))}
        </div>
        <RoleGuard min="system_admin">
          <div className="mt-6 flex items-center gap-3">
            <Button
              onClick={() => saveMutation.mutate([...draft])}
              disabled={saveMutation.isPending}
              data-testid="platform-save"
            >
              {saveMutation.isPending ? t("saving") : t("save")}
            </Button>
            {saveMutation.isError ? (
              <span className="text-destructive text-xs" data-testid="platform-save-error">
                {errorText(saveMutation.error)}
              </span>
            ) : null}
          </div>
        </RoleGuard>
      </CardContent>
    </Card>
  );
}
