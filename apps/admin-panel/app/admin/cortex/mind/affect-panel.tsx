"use client";

/**
 * Los cuatro paneles que pintan el afecto simulado: diales PAD, drives, la curva
 * de mood en el tiempo (SVG a mano — el panel no tiene librería de charts) y los
 * episodios con su `appraisal_reason`.
 *
 * Salen de `page.tsx` en `task_prod16_08`. No tienen estado propio ni consultan
 * nada: reciben el `CortexMind` ya resuelto, así que el corte es limpio.
 */

import { useMemo } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import {
  driveToPercent,
  padToPercent,
  type CortexAffectPoint,
  type CortexEpisode,
  type CortexMind,
  type PadDimension,
} from "@/lib/cortex";

// ---------------------------------------------------------------------------
// Diales PAD + mood
// ---------------------------------------------------------------------------
const PAD_DIMENSIONS: {
  key: PadDimension;
  label: string;
  testid: string;
  /** Una dimensión bipolar [-1,1] marca su punto neutro (0) en el centro. */
  bipolar: boolean;
}[] = [
  { key: "valence", label: "Valencia", testid: "pad-valence", bipolar: true },
  { key: "arousal", label: "Activación", testid: "pad-arousal", bipolar: false },
  { key: "dominance", label: "Dominancia", testid: "pad-dominance", bipolar: true },
  { key: "intensity", label: "Intensidad", testid: "pad-intensity", bipolar: false },
];

export function PadPanel({ mind }: { mind: CortexMind }) {
  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-muted-foreground text-xs uppercase tracking-wider">
            Emoción (PAD) y mood
          </p>
          <span
            className="bg-primary/10 text-primary inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold"
            data-testid="mood-label"
          >
            {mind.mood_label || "—"}
          </span>
        </div>
        <div className="space-y-3">
          {PAD_DIMENSIONS.map((d) => (
            <PadDial
              key={d.key}
              label={d.label}
              testid={d.testid}
              value={mind[d.key]}
              percent={padToPercent(d.key, mind[d.key])}
              bipolar={d.bipolar}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function PadDial({
  label,
  testid,
  value,
  percent,
  bipolar,
}: {
  label: string;
  testid: string;
  value: number;
  percent: number;
  bipolar: boolean;
}) {
  return (
    <div data-testid={testid}>
      <div className="mb-1 flex items-center justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div className="bg-muted relative h-2.5 w-full overflow-hidden rounded-full">
        {/* Punto neutro (0) de una dimensión bipolar, marca visual al 50%. */}
        {bipolar ? (
          <span
            aria-hidden="true"
            className="bg-border absolute inset-y-0 left-1/2 w-px -translate-x-1/2"
          />
        ) : null}
        <div
          className="bg-primary absolute inset-y-0 left-0 rounded-full transition-[width] duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Drives ("sensaciones")
// ---------------------------------------------------------------------------
const DRIVE_DIMENSIONS: { key: keyof CortexMind["drives"]; label: string }[] = [
  { key: "curiosity", label: "Curiosidad" },
  { key: "bonding", label: "Vínculo" },
  { key: "coherence", label: "Coherencia" },
  { key: "competence", label: "Competencia" },
];

export function DrivesPanel({ mind }: { mind: CortexMind }) {
  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <p className="text-muted-foreground text-xs uppercase tracking-wider">
          Sensaciones (drives)
        </p>
        <div className="space-y-3" data-testid="drives">
          {DRIVE_DIMENSIONS.map((d) => {
            const value = mind.drives[d.key];
            return (
              <div key={d.key} data-testid={`drive-${d.key}`}>
                <div className="mb-1 flex items-center justify-between text-sm">
                  <span>{d.label}</span>
                  <span className="text-muted-foreground tabular-nums">{value.toFixed(2)}</span>
                </div>
                <div className="bg-muted relative h-2.5 w-full overflow-hidden rounded-full">
                  <div
                    className="bg-info absolute inset-y-0 left-0 rounded-full transition-[width] duration-300"
                    style={{ width: `${driveToPercent(value)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

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
          Mood en el tiempo (valencia del mood)
        </p>
        {isLoading ? (
          <div className="flex items-center justify-center py-10">
            <Spinner />
          </div>
        ) : isError ? (
          <p className="text-destructive text-sm" data-testid="mood-chart-error">
            No se pudo cargar la serie temporal.
          </p>
        ) : points.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="mood-chart-empty">
            Aún no hay snapshots afectivos. Conversa con el córtex para empezar a registrar su mood.
          </p>
        ) : (
          <svg
            data-testid="mood-chart"
            viewBox={`0 0 ${width} ${height}`}
            className="text-primary h-36 w-full"
            role="img"
            aria-label="Evolución de la valencia del mood en el tiempo"
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
  return (
    <Card>
      <CardContent className="pt-5">
        <p className="text-muted-foreground mb-3 text-xs uppercase tracking-wider">
          Episodios recientes
        </p>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Spinner />
          </div>
        ) : isError ? (
          <p className="text-destructive text-sm" data-testid="episodes-error">
            No se pudieron cargar los episodios.
          </p>
        ) : episodes.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="episodes-empty">
            Sin episodios emocionales todavía.
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
  const when = formatWhen(episode.created_at);
  return (
    <li
      className="border-border rounded-lg border p-3"
      data-testid={`episode-${episode.id}`}
      // El motivo del appraisal asoma al pasar el ratón (además de expandir).
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
              <span className="text-foreground font-medium">Motivo:</span>{" "}
              {episode.appraisal_reason}
            </p>
          ) : (
            <p className="text-muted-foreground italic">Sin motivo registrado.</p>
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
