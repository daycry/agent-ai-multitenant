"use client";

/**
 * Panel de Mente — el estado afectivo del córtex en vivo (Córtex F2, ADR 0075).
 *
 * Vista HERMANA del chat del córtex (`app/admin/cortex/page.tsx`): mismo gate
 * (`isSystemOwner`; un no-owner ve `cortex-mind-no-access` y el backend
 * `require_system_owner` es la barrera real, ADR 0074). Muestra al dueño del
 * despliegue:
 *
 *   - Diales PAD: valence / arousal / dominance / intensity + la `mood_label`
 *     destacada (la EMOCIÓN viva, capa rápida).
 *   - Drives ("sensaciones"/necesidades): curiosity / bonding / coherence /
 *     competence ∈ [0,1].
 *   - Gráfico de mood en el tiempo (SVG puro, sin librería de charts — no hay
 *     ninguna en el panel; ver reporte de desviaciones).
 *   - Episodios: memorias emocionales recientes; al expandir/hover muestran su
 *     `appraisal_reason` (POR QUÉ el córtex sintió eso).
 *   - WS en vivo (`/ws/owner/cortex/telemetry`): cada frame `type:'affect'`
 *     actualiza los diales; si el WS cae, polling de `/mind` cada 10s mantiene
 *     el estado fresco.
 *
 * Honestidad de producto OBLIGATORIA (ADR 0075 §6): es una SIMULACIÓN
 * computacional de afecto determinista, NO emociones ni consciencia reales. El
 * banner de honestidad (copy del bloque `honesty` de `/mind`) se muestra SIEMPRE
 * y bien visible — no se vende como sentimientos reales.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Brain, Info } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import {
  affectFrameToMind,
  driveToPercent,
  getCortexAffectTimeseries,
  getCortexAutonomy,
  getCortexEpisodes,
  getCortexMind,
  getCortexPursuits,
  padToPercent,
  setCortexAutonomy,
  type CortexAffectPoint,
  type CortexAutonomy,
  type CortexEpisode,
  type CortexMind,
  type CortexPursuit,
  type PadDimension,
} from "@/lib/cortex";
import { useCurrentUser } from "@/lib/use-current-user";
import { useWebSocket, wsUrl } from "@/lib/ws";

const POLL_INTERVAL_MS = 10_000;

export default function CortexMindPage() {
  const { isSystemOwner, isLoading: userLoading } = useCurrentUser();

  // Mientras no sabemos el rol, nada: nunca parpadear contenido owner-only
  // antes de saber si el usuario puede verlo.
  if (userLoading) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Spinner />
          Cargando…
        </p>
      </div>
    );
  }

  // No-owner -> sin diales. El backend (require_system_owner) es la barrera real.
  if (!isSystemOwner) {
    return <CortexMindNoAccess />;
  }

  return <CortexMindBody />;
}

function CortexMindBody() {
  // Estado vivo de los diales: arranca del último /mind y se pisa con cada frame
  // WS. El polling re-sincroniza /mind por si el WS cae o pierde un frame.
  const [live, setLive] = useState<CortexMind | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const mindQuery = useQuery<CortexMind, ApiError>({
    queryKey: ["cortex", "mind"],
    queryFn: getCortexMind,
    refetchOnWindowFocus: false,
    retry: false,
    refetchInterval: POLL_INTERVAL_MS,
  });

  const timeseriesQuery = useQuery<CortexAffectPoint[], ApiError>({
    queryKey: ["cortex", "affect-timeseries"],
    queryFn: () => getCortexAffectTimeseries({ limit: 500 }),
    refetchOnWindowFocus: false,
    retry: false,
    refetchInterval: POLL_INTERVAL_MS,
  });

  const episodesQuery = useQuery<CortexEpisode[], ApiError>({
    queryKey: ["cortex", "episodes"],
    queryFn: () => getCortexEpisodes({ limit: 50 }),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const pursuitsQuery = useQuery<CortexPursuit[], ApiError>({
    queryKey: ["cortex", "pursuits"],
    queryFn: () => getCortexPursuits({ limit: 20 }),
    refetchOnWindowFocus: false,
    retry: false,
    refetchInterval: POLL_INTERVAL_MS,
  });

  // Un 403 en cualquier consulta = dejaste de ser owner tras cargar: refleja el
  // gate del backend en vez de mostrar diales que sabemos denegados.
  useEffect(() => {
    const err = mindQuery.error ?? timeseriesQuery.error ?? episodesQuery.error;
    if (err instanceof ApiError && err.status === 403) setForbidden(true);
  }, [mindQuery.error, timeseriesQuery.error, episodesQuery.error]);

  // Adopta el último /mind como base de los diales (el WS lo pisa al instante).
  useEffect(() => {
    if (mindQuery.data) setLive(mindQuery.data);
  }, [mindQuery.data]);

  // WS de telemetría: cada frame `type:'affect'` actualiza los diales en vivo
  // (~1-2s tras la respuesta, appraisal asíncrono). `useWebSocket` auto-reconecta
  // con backoff; el polling de /mind es el fallback cuando el socket está caído.
  useWebSocket(wsUrl("/ws/owner/cortex/telemetry"), (data) => {
    const next = affectFrameToMind(data);
    if (!next) return;
    setLive((prev) =>
      // Conserva el bloque honesty del último /mind (el frame no lo trae).
      prev ? { ...next, honesty: prev.honesty } : next,
    );
  });

  if (forbidden) {
    return <CortexMindNoAccess />;
  }

  if (mindQuery.isLoading && !live) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <MindHeader />
        <div className="mt-6 flex items-center justify-center py-16">
          <Spinner />
        </div>
      </div>
    );
  }

  if (mindQuery.isError && !live) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <MindHeader />
        <Card className="mt-6">
          <CardContent className="text-destructive pt-5 text-sm" data-testid="cortex-mind-error">
            No se pudo cargar el estado del córtex:{" "}
            {mindQuery.error instanceof ApiError ? mindQuery.error.body : String(mindQuery.error)}
          </CardContent>
        </Card>
      </div>
    );
  }

  const mind = live;

  return (
    <div
      className="mx-auto flex w-full max-w-5xl flex-col px-4 py-8 sm:px-6 lg:px-8"
      data-testid="cortex-mind"
    >
      <MindHeader />

      {/* Copy honesto OBLIGATORIO y bien visible (ADR 0075 §6). */}
      <HonestyBanner mind={mind} />

      {mind ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <PadPanel mind={mind} />
          <DrivesPanel mind={mind} />
        </div>
      ) : null}

      <div className="mt-6">
        <MoodChart
          points={timeseriesQuery.data ?? []}
          isLoading={timeseriesQuery.isLoading}
          isError={timeseriesQuery.isError}
        />
      </div>

      <div className="mt-6">
        <EpisodesPanel
          episodes={episodesQuery.data ?? []}
          isLoading={episodesQuery.isLoading}
          isError={episodesQuery.isError}
        />
      </div>

      <div className="mt-6">
        <LearningPanel
          pursuits={pursuitsQuery.data ?? []}
          isLoading={pursuitsQuery.isLoading}
          isError={pursuitsQuery.isError}
        />
      </div>

      <div className="mt-6">
        <AutonomyPanel />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Autonomía — kill-switch de los bucles de fondo + gates + budget (ADR 0078)
// ---------------------------------------------------------------------------
function AutonomyPanel() {
  const queryClient = useQueryClient();
  const autonomyQuery = useQuery<CortexAutonomy, ApiError>({
    queryKey: ["cortex", "autonomy"],
    queryFn: getCortexAutonomy,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const toggleMutation = useMutation<
    CortexAutonomy,
    ApiError,
    { autonomy_enabled?: boolean; web_enabled?: boolean }
  >({
    mutationFn: setCortexAutonomy,
    onSuccess: (data) => queryClient.setQueryData(["cortex", "autonomy"], data),
  });

  const autonomy = autonomyQuery.data;

  return (
    <Card data-testid="cortex-autonomy-panel">
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Autonomía (bucles de fondo)</h2>
          <span className="text-muted-foreground text-xs">
            Curiosidad y reflexión programadas — con budget y kill-switch (ADR 0078)
          </span>
        </div>
        {autonomyQuery.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            Cargando…
          </p>
        ) : autonomyQuery.isError || !autonomy ? (
          <p className="text-destructive text-sm">No se pudo cargar el estado de autonomía.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                size="sm"
                variant={autonomy.autonomy_enabled ? "destructive" : "default"}
                data-testid="cortex-autonomy-toggle"
                disabled={toggleMutation.isPending}
                onClick={() =>
                  toggleMutation.mutate({ autonomy_enabled: !autonomy.autonomy_enabled })
                }
              >
                {autonomy.autonomy_enabled ? "Apagar autonomía" : "Encender autonomía"}
              </Button>
              <span className="text-sm" data-testid="cortex-autonomy-state">
                Estado:{" "}
                <span
                  className={
                    autonomy.autonomy_enabled
                      ? "font-medium text-emerald-600 dark:text-emerald-400"
                      : "text-muted-foreground font-medium"
                  }
                >
                  {autonomy.autonomy_enabled ? "ENCENDIDA" : "APAGADA"}
                </span>
              </span>
              <Button
                size="sm"
                variant="outline"
                data-testid="cortex-web-toggle"
                disabled={toggleMutation.isPending}
                onClick={() => toggleMutation.mutate({ web_enabled: !autonomy.web_enabled })}
              >
                {autonomy.web_enabled ? "Deshabilitar web" : "Habilitar web"}
              </Button>
              {toggleMutation.isError ? (
                <span className="text-destructive text-xs">No se pudo cambiar el estado.</span>
              ) : null}
            </div>
            <ul className="text-muted-foreground grid gap-1 text-xs sm:grid-cols-2">
              <li>
                Web del córtex:{" "}
                <span className="text-foreground font-medium" data-testid="cortex-web-state">
                  {autonomy.web_enabled ? "habilitada" : "deshabilitada"}
                </span>{" "}
                (búsqueda/lectura web vía egress-proxy con anti-SSRF)
              </li>
              <li>
                Búsquedas de curiosidad hoy:{" "}
                <span className="text-foreground font-medium">
                  {autonomy.budget.searches_today} / {autonomy.budget.searches_cap}
                </span>
              </li>
              <li>
                Umbral del drive de curiosidad:{" "}
                <span className="text-foreground font-medium">
                  {autonomy.curiosity_drive_threshold}
                </span>
              </li>
              <li>
                Circuit-breaker:{" "}
                <span className="text-foreground font-medium">
                  {autonomy.circuit_breaker_open ? "abierto (pausado)" : "cerrado (ok)"}
                </span>
              </li>
            </ul>
            <p className="text-muted-foreground text-xs">{autonomy.note_es}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Lo que está aprendiendo — historial de curiosidad (ADR 0078, copy honesto)
// ---------------------------------------------------------------------------

/** Etiqueta ES por estado del ciclo de vida de un pursuit. */
const PURSUIT_STATUS_LABELS: Record<string, string> = {
  selected: "elegido",
  searching: "investigando",
  digested: "aprendido — pendiente de contarlo",
  surfaced: "comentado en conversación",
  skipped: "descartado",
  failed: "falló",
};

function LearningPanel({
  pursuits,
  isLoading,
  isError,
}: {
  pursuits: CortexPursuit[];
  isLoading: boolean;
  isError: boolean;
}) {
  return (
    <Card data-testid="cortex-learning-panel">
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Lo que está aprendiendo</h2>
          <span className="text-muted-foreground text-xs">
            Bucle de curiosidad programado — no es curiosidad consciente
          </span>
        </div>
        {isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Spinner />
            Cargando…
          </p>
        ) : isError ? (
          <p className="text-destructive text-sm">No se pudo cargar el historial de curiosidad.</p>
        ) : pursuits.length === 0 ? (
          <EmptyState
            title="Aún no hay temas"
            description="Cuando el bucle de curiosidad investigue un tema que menciones, aparecerá aquí y el córtex lo sacará en la próxima conversación."
          />
        ) : (
          <ul className="divide-border divide-y" data-testid="cortex-learning-list">
            {pursuits.map((pursuit) => (
              <li key={pursuit.id} className="flex items-center justify-between gap-3 py-2">
                <span className="truncate text-sm">{pursuit.topic}</span>
                <span
                  className={
                    pursuit.status === "surfaced"
                      ? "text-muted-foreground shrink-0 text-xs"
                      : "shrink-0 text-xs font-medium text-emerald-600 dark:text-emerald-400"
                  }
                >
                  {PURSUIT_STATUS_LABELS[pursuit.status] ?? pursuit.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function MindHeader() {
  return (
    <PageHeader
      icon={<Activity className="h-6 w-6 sm:h-7 sm:w-7" />}
      title="Panel de Mente"
      description="El estado afectivo del córtex en vivo: emoción (PAD), mood, sensaciones (drives), evolución temporal y episodios. Es una simulación computacional, no sentimientos reales."
      data-testid="cortex-mind-header"
    />
  );
}

/**
 * Banner de honestidad — NO removible (ADR 0075 §6). Usa el copy del bloque
 * `honesty` que devuelve `/mind` (ES; cae a EN si falta); si aún no hay estado,
 * un texto por defecto equivalente para que NUNCA se muestren diales sin él.
 */
function HonestyBanner({ mind }: { mind: CortexMind | null }) {
  const note =
    mind?.honesty.note_es?.trim() ||
    mind?.honesty.note_en?.trim() ||
    "Modelo computacional de afecto, no sentimientos reales.";
  return (
    <div
      className="border-border bg-muted text-muted-foreground mt-4 flex items-start gap-2 rounded-lg border px-4 py-3 text-sm"
      role="note"
      data-testid="cortex-mind-honesty"
    >
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <p>
        <span className="text-foreground font-medium">Aviso de honestidad:</span> {note} Lo que ves
        es una simulación determinista del afecto del córtex; no se vende como emociones ni
        consciencia reales.
      </p>
    </div>
  );
}

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

function PadPanel({ mind }: { mind: CortexMind }) {
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

function DrivesPanel({ mind }: { mind: CortexMind }) {
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
function MoodChart({
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
function EpisodesPanel({
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

function CortexMindNoAccess() {
  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Panel de Mente"
        data-testid="cortex-mind-header"
      />
      <EmptyState
        data-testid="cortex-mind-no-access"
        icon={Brain}
        title="Panel de Mente no disponible"
        description="El Panel de Mente es exclusivo del System Owner (el dueño del despliegue). Tu cuenta no tiene ese rol."
      />
    </div>
  );
}
