"use client";

/**
 * «Activar una base existente» en la pestaña Conocimiento del proyecto
 * (`task_ui_21`): concede al proyecto una base que ya existe —las del catálogo
 * global (`is_builtin`) incluidas— SIN subir ningún documento. El asistente no las
 * ofrece como destino (son de sólo lectura), así que sin esto no habría forma de
 * activarlas desde la pestaña. Quitarlas es «Quitar del proyecto» de su tarjeta.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { KbRef } from "./kb-types";

export function ActivateExistingKb({
  projectId,
  grantedKbIds,
  grantedReady,
}: {
  projectId: string;
  grantedKbIds: ReadonlySet<string>;
  /** El listado de bases del proyecto cargó SIN error: sin él no se sabe qué está ya concedido. */
  grantedReady: boolean;
}) {
  const t = useT("knowledgeWizard");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const catalogQuery = useQuery({
    queryKey: ["kb-catalog"],
    queryFn: () => apiFetch<KbRef[]>("/knowledge-bases"),
    refetchOnWindowFocus: false,
  });
  const activate = useMutation({
    mutationFn: (kbId: string) =>
      apiFetch(`/knowledge-bases/${kbId}/projects`, {
        method: "POST",
        body: { project_id: projectId },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project-kbs", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["kb-catalog"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });

  const candidates = grantedReady
    ? (catalogQuery.data ?? []).filter((k) => !grantedKbIds.has(k.id))
    : [];
  if (candidates.length === 0) return null;

  return (
    <RoleGuard min="tenant_admin">
      <Card className="mt-6" data-testid="kb-activate-section">
        <CardContent className="pt-5">
          <h2 className="text-base font-semibold">{t("activateTitle")}</h2>
          <p className="text-muted-foreground mt-1 text-sm">{t("activateHint")}</p>
          {activate.isError ? (
            <p className="text-destructive mt-2 text-sm" data-testid="kb-activate-error">
              {errorText(activate.error)}
            </p>
          ) : null}
          <ul className="mt-3 divide-y">
            {candidates.map((kb) => (
              <li key={kb.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <span className="flex items-center gap-2 truncate text-sm">
                    {kb.name}
                    {kb.is_builtin ? <Badge variant="muted">{t("builtinBadge")}</Badge> : null}
                  </span>
                  {kb.description ? (
                    <span className="text-muted-foreground block truncate text-xs">
                      {kb.description}
                    </span>
                  ) : null}
                </div>
                <Button
                  size="sm"
                  disabled={activate.isPending}
                  onClick={() => activate.mutate(kb.id)}
                  data-testid={`kb-activate-${kb.id}`}
                >
                  {t("activate")}
                </Button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </RoleGuard>
  );
}
