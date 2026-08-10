"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { ApiError } from "@/lib/api";
import {
  approveBrowseSession,
  browseStepSummary,
  getBrowseSessions,
  rejectBrowseSession,
  type BrowseSession,
} from "@/lib/cortex";

// ---------------------------------------------------------------------------
// Inbox de aprobación de navegación (ADR 0080) — validación humana POR SESIÓN
// ---------------------------------------------------------------------------

export function BrowseInboxPanel() {
  const queryClient = useQueryClient();
  const sessionsQuery = useQuery<BrowseSession[], ApiError>({
    queryKey: ["cortex", "browse-sessions"],
    queryFn: getBrowseSessions,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const decide = useMutation<BrowseSession, ApiError, { id: string; approve: boolean }>({
    mutationFn: ({ id, approve }) => (approve ? approveBrowseSession(id) : rejectBrowseSession(id)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cortex", "browse-sessions"] }),
  });

  const sessions = sessionsQuery.data ?? [];

  return (
    <Card data-testid="cortex-browse-inbox">
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Navegación pendiente de tu aprobación</h2>
          <span className="text-muted-foreground text-xs">
            El córtex PIDE navegar; tú apruebas cada sesión viendo el guion exacto (ADR 0080)
          </span>
        </div>
        {sessionsQuery.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            Cargando…
          </p>
        ) : sessionsQuery.isError ? (
          <p className="text-destructive text-sm">No se pudo cargar el inbox de navegación.</p>
        ) : sessions.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="cortex-browse-empty">
            No hay sesiones de navegación pendientes.
          </p>
        ) : (
          <ul className="space-y-3" data-testid="cortex-browse-list">
            {sessions.map((s) => (
              <li key={s.id} className="rounded-md border p-3" data-testid="cortex-browse-item">
                <p className="text-sm font-medium">{s.goal}</p>
                <ol className="text-muted-foreground mt-1 list-decimal space-y-0.5 pl-5 text-xs">
                  {s.steps.map((step, i) => (
                    <li key={i}>{browseStepSummary(step)}</li>
                  ))}
                </ol>
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    data-testid="cortex-browse-approve"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: s.id, approve: true })}
                  >
                    Aprobar y navegar
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    data-testid="cortex-browse-reject"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ id: s.id, approve: false })}
                  >
                    Rechazar
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {decide.isError ? (
          <p className="text-destructive text-xs">No se pudo aplicar la decisión.</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
