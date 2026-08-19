"use client";

/**
 * Las dos piezas HISTÓRICAS del afecto simulado: la curva de mood en el tiempo
 * (SVG a mano — el panel no tiene librería de charts) y los episodios con su
 * `appraisal_reason`.
 *
 * Salen de `page.tsx` en `task_prod16_08`. No tienen estado propio ni consultan
 * nada: reciben los datos ya resueltos, así que el corte es limpio.
 *
 * Los diales PAD y los drives estuvieron aquí y se fueron a
 * `components/cortex/mind-panel.tsx` al cerrar la casilla F2 del Panel de Mente:
 * son el estado VIVO, se montan también en la segunda columna del chat, y tenían
 * que viajar pegados al aviso honesto (ADR 0075 §6) para que no exista forma de
 * pintar un dial sin él. Lo de aquí es el histórico y sólo se ve en la pantalla
 * completa.
 *
 * Todo el copy va por el diccionario (`cortexMind`): el panel era ES-only y por
 * eso su casilla seguía abierta.
 */

import { useMemo } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { type CortexAffectPoint, type CortexEpisode } from "@/lib/cortex";
import { useT } from "@/lib/i18n";

// ---------------------------------------------------------------------------
// Gráfico de mood en el tiempo — SVG puro (no hay librería de charts en el panel)
// ---------------------------------------------------------------------------
export function MoodChart({
  points,
  isLoading,
  isError,
}: {
  points: CortexAffectPoint[];
  isLoading: boolean;
  isError: boolean;
}) {
  const t = useT("cortexMind");
  // La línea sigue la VALENCIA del mood (capa lenta) ∈ [-1,1] re-escalada a
  // [0,1] para el dibujo; es el indicador de "humor general" más legible.
  const width = 720;
  const height = 140;
  const pad = 8;

  const line = useMemo(() => {
    const n = points.length;
    if (n === 0) return "";
    const stepX = n > 1 ? (width - 2 * pad) / (n - 1) : 0;
    return points
      .map((p, i) => {
        const x = pad + i * stepX;
        // mood_valence ∈ [-1,1] -> [0,1] (1 arriba, -1 abajo).
        const norm = (Math.min(1, Math.max(-1, p.mood_valence)) + 1) / 2;
        const y = height - pad - norm * (height - 2 * pad);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [points]);

  return (
    <Card>
      <CardContent className="pt-5">
        <p className="text-muted-foreground mb-2 text-xs uppercase tracking-wider">
          {t("moodChartTitle")}
        </p>
        {isLoading ? (
          <div className="flex items-center justify-center py-10">
            <Spinner />
          </div>
        ) : isError ? (
          <p className="text-destructive text-sm" data-testid="mood-chart-error">
            {t("moodChartError")}
          </p>
        ) : points.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="mood-chart-empty">
            {t("moodChartEmpty")}
          </p>
        ) : (
          <svg
            data-testid="mood-chart"
            viewBox={`0 0 ${width} ${height}`}
            className="text-primary h-36 w-full"
            role="img"
            aria-label={t("moodChartAria")}
            preserveAspectRatio="none"
          >
            {/* Línea neutra (valencia 0) al centro vertical. */}
            <line
              x1={pad}
              y1={height / 2}
              x2={width - pad}
              y2={height / 2}
              className="text-border"
              stroke="currentColor"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <polyline points={line} fill="none" stroke="currentColor" strokeWidth={2} />
          </svg>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Episodios — lista con `appraisal_reason` al expandir
// ---------------------------------------------------------------------------
export function EpisodesPanel({
  episodes,
  isLoading,
  isError,
}: {
  episodes: CortexEpisode[];
  isLoading: boolean;
  isError: boolean;
}) {
  const t = useT("cortexMind");
  return (
    <Card>
      <CardContent className="pt-5">
        <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
          {t("episodesTitle")}
        </p>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Spinner />
          </div>
        ) : isError ? (
          <p className="text-destructive text-sm" data-testid="episodes-error">
            {t("episodesError")}
          </p>
        ) : episodes.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="episodes-empty">
            {t("episodesEmpty")}
          </p>
        ) : (
          <ul className="space-y-2" data-testid="episodes">
            {episodes.map((ep) => (
              <EpisodeRow key={ep.id} episode={ep} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function EpisodeRow({ episode }: { episode: CortexEpisode }) {
  const t = useT("cortexMind");
  const when = formatWhen(episode.created_at);
  return (
    <li
      className="border-border rounded-lg border p-3"
      data-testid={`episode-${episode.id}`}
      // El motivo del appraisal asoma al pasar el ratón (además de expandir). Es
      // texto del BACKEND, no de la UI: por eso no va por el diccionario.
      title={episode.appraisal_reason ?? undefined}
    >
      <details>
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
          <span className="min-w-0 flex-1 truncate text-sm">{episode.content}</span>
          {episode.mood_label ? (
            <span className="bg-muted text-muted-foreground inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs">
              {episode.mood_label}
            </span>
          ) : null}
          <span className="text-muted-foreground shrink-0 text-xs tabular-nums">{when}</span>
        </summary>
        <div className="mt-2 space-y-1 text-sm">
          {episode.appraisal_reason ? (
            <p className="text-muted-foreground" data-testid={`episode-reason-${episode.id}`}>
              <span className="text-foreground font-medium">{t("episodeReasonLabel")}</span>{" "}
              {episode.appraisal_reason}
            </p>
          ) : (
            <p className="text-muted-foreground italic">{t("episodeNoReason")}</p>
          )}
          <p className="text-muted-foreground text-xs tabular-nums">
            V {fmtNum(episode.valence)} · A {fmtNum(episode.arousal)} · D{" "}
            {fmtNum(episode.dominance)} · I {fmtNum(episode.intensity)}
          </p>
        </div>
      </details>
    </li>
  );
}

function fmtNum(value: number | null): string {
  return value === null || Number.isNaN(value) ? "—" : value.toFixed(2);
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}
