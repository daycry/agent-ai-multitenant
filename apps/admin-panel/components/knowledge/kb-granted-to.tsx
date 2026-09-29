"use client";

/**
 * «Concedida a» de una base (`task_ui_21`): los proyectos que la leen, con
 * «Retirar» en cada uno para quien administra. Conceder nuevos proyectos sigue
 * siendo el botón de la tarjeta (`KbGrantDialog`); los dos comparten endpoint.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";

import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { KbGrantedProject } from "./kb-types";

/**
 * `grants` viene en el listado de bases (una consulta por lotes en el backend),
 * no de una petición por fila.
 */
export function KbGrantedTo({
  kbId,
  grants = [],
}: {
  kbId: string;
  grants?: KbGrantedProject[] | null;
}) {
  const t = useT("knowledgeWizard");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: (projectId: string) =>
      apiFetch<void>(`/knowledge-bases/${kbId}/projects/${projectId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["kb-projects", kbId] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });
  const list = grants ?? [];

  return (
    <div
      className="mt-2 flex flex-wrap items-center gap-1.5 text-xs"
      data-testid={`kb-granted-${kbId}`}
    >
      <span className="text-muted-foreground">{t("grantedTo")}</span>
      {list.length === 0 ? (
        <span className="text-muted-foreground italic" data-testid={`kb-granted-none-${kbId}`}>
          {t("grantedNone")}
        </span>
      ) : (
        list.map((g) => (
          <span
            key={g.project_id}
            className="bg-muted inline-flex items-center gap-1 rounded px-1.5 py-0.5"
            data-testid={`kb-granted-${kbId}-${g.project_id}`}
          >
            {g.name}
            <RoleGuard min="tenant_admin">
              <button
                type="button"
                aria-label={t("revoke", { name: g.name })}
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(g.project_id)}
                data-testid={`kb-revoke-${kbId}-${g.project_id}`}
              >
                <X className="h-3 w-3" />
              </button>
            </RoleGuard>
          </span>
        ))
      )}
      {revoke.isError ? (
        <span className="text-destructive" data-testid={`kb-revoke-error-${kbId}`}>
          {errorText(revoke.error)}
        </span>
      ) : null}
    </div>
  );
}
