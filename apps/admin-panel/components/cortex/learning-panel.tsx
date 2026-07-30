"use client";

/**
 * «Lo que está aprendiendo» — historial de curiosidad del córtex (Córtex F4,
 * ADR 0078).
 *
 * Vive fuera de la página para que sus dos piezas delicadas tengan test propio:
 *
 *   - el **owner-approval gate**: mientras el bucle deja un tema en `selected`
 *     sin decisión, el owner aprueba o rechaza AQUÍ (mismo endpoint, `approved`
 *     en el cuerpo). Los botones desaparecen en cuanto hay decisión: una segunda
 *     aprobación sería un segundo gasto.
 *   - el **copy honesto bilingüe**: la API manda `note_es` y `note_en`; se
 *     muestra la del idioma activo, y si el backend no manda ninguna se usa un
 *     texto propio — el aviso del ADR 0075 §6 NUNCA puede quedar en blanco.
 *
 * Los estados y el formato salen de los helpers puros de `lib/cortex-curiosity.ts`.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Spinner } from "@/components/ui/spinner";
import type { ApiError } from "@/lib/api";
import { decideCortexPursuit, type CortexPursuit } from "@/lib/cortex";
import {
  honestNote,
  pursuitAwaitsApproval,
  pursuitStatusLabel,
  type CortexLang,
} from "@/lib/cortex-curiosity";
import { useLangOptional } from "@/lib/lang-context";

/** Fallback del aviso honesto cuando el backend no manda ninguna nota. */
const FALLBACK_HONESTY: Record<CortexLang, string> = {
  es: "Bucle de curiosidad programado con topes de coste — no es curiosidad consciente.",
  en: "Scheduled curiosity loop under cost caps — this is not conscious curiosity.",
};

const COPY: Record<CortexLang, Record<string, string>> = {
  es: {
    title: "Lo que está aprendiendo",
    loading: "Cargando…",
    error: "No se pudo cargar el historial de curiosidad.",
    emptyTitle: "Aún no hay temas",
    emptyBody:
      "Cuando el bucle de curiosidad investigue un tema que menciones, aparecerá aquí y el córtex lo sacará en la próxima conversación.",
    awaiting: "esperando tu aprobación",
    approve: "Aprobar",
    reject: "Rechazar",
    decideError: "No se pudo registrar la decisión.",
  },
  en: {
    title: "What it is learning",
    loading: "Loading…",
    error: "Could not load the curiosity history.",
    emptyTitle: "No topics yet",
    emptyBody:
      "Once the curiosity loop researches a topic you mentioned, it will show up here and the cortex will bring it up in your next conversation.",
    awaiting: "waiting for your approval",
    approve: "Approve",
    reject: "Reject",
    decideError: "Could not record the decision.",
  },
};

export function LearningPanel({
  pursuits,
  isLoading,
  isError,
  honesty,
}: {
  pursuits: CortexPursuit[];
  isLoading: boolean;
  isError: boolean;
  /** Bloque `{note_es, note_en}` del endpoint (`/autonomy`), si está cargado. */
  honesty?: { note_es?: string | null; note_en?: string | null };
}) {
  const lang = useLangOptional();
  const copy = COPY[lang];
  const queryClient = useQueryClient();

  const decide = useMutation<CortexPursuit, ApiError, { id: string; approved: boolean }>({
    mutationFn: ({ id, approved }) => decideCortexPursuit(id, approved),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cortex", "pursuits"] }),
  });

  const note = honestNote(honesty ?? {}, lang) || FALLBACK_HONESTY[lang];

  return (
    <Card data-testid="cortex-learning-panel">
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">{copy.title}</h2>
          <span className="text-muted-foreground text-xs" data-testid="cortex-learning-honesty">
            {note}
          </span>
        </div>
        {isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            {copy.loading}
          </p>
        ) : isError ? (
          <p className="text-destructive text-sm">{copy.error}</p>
        ) : pursuits.length === 0 ? (
          <EmptyState title={copy.emptyTitle} description={copy.emptyBody} />
        ) : (
          <ul className="divide-border divide-y" data-testid="cortex-learning-list">
            {pursuits.map((pursuit) => {
              const awaiting = pursuitAwaitsApproval(pursuit);
              return (
                <li
                  key={pursuit.id}
                  className="flex flex-wrap items-center justify-between gap-3 py-2"
                >
                  <span className="min-w-0 flex-1 truncate text-sm">{pursuit.topic}</span>
                  {typeof pursuit.cost_usd === "number" ? (
                    <span
                      className="text-muted-foreground shrink-0 text-xs tabular-nums"
                      data-testid="cortex-pursuit-cost"
                    >
                      {pursuit.cost_usd} $
                    </span>
                  ) : null}
                  {awaiting ? (
                    <span
                      className="shrink-0 text-xs font-medium text-amber-600 dark:text-amber-400"
                      data-testid={`cortex-pursuit-pending-${pursuit.id}`}
                    >
                      {copy.awaiting}
                    </span>
                  ) : (
                    <span
                      className={
                        pursuit.status === "surfaced"
                          ? "text-muted-foreground shrink-0 text-xs"
                          : "shrink-0 text-xs font-medium text-emerald-600 dark:text-emerald-400"
                      }
                    >
                      {pursuitStatusLabel(pursuit.status, lang)}
                    </span>
                  )}
                  {awaiting ? (
                    <span className="flex shrink-0 gap-2">
                      <Button
                        size="sm"
                        data-testid="cortex-pursuit-approve"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: pursuit.id, approved: true })}
                      >
                        {copy.approve}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        data-testid="cortex-pursuit-reject"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: pursuit.id, approved: false })}
                      >
                        {copy.reject}
                      </Button>
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        {decide.isError ? (
          <p className="text-destructive text-xs" data-testid="cortex-pursuit-decide-error">
            {copy.decideError}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
