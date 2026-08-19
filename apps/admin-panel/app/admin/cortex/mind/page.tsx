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

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Brain } from "lucide-react";

import { LearningPanel } from "@/components/cortex/learning-panel";
import { MindPadSpace } from "@/components/cortex/mind-pad-space";
import { MindPanel } from "@/components/cortex/mind-panel";
import { PageHeader } from "@/components/layout/page-header";
import { useT } from "@/lib/i18n";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import {
  affectFrameToMind,
  getCortexAffectTimeseries,
  getCortexEpisodes,
  getCortexMind,
  cortexFetch,
  getCortexPursuits,
  type CortexAffectPoint,
  type CortexEpisode,
  type CortexMind,
  type CortexPursuit,
} from "@/lib/cortex";
import { useCurrentUser } from "@/lib/use-current-user";
import { useWebSocket, wsUrl } from "@/lib/ws";

import { EpisodesPanel, MoodChart } from "./affect-panel";
import { AutonomyPanel } from "./autonomy-panel";
import { BrowseInboxPanel } from "./browse-inbox-panel";
import { JournalPanel, type CortexJournalEntry } from "./journal-panel";
const POLL_INTERVAL_MS = 10_000;

export default function CortexMindPage() {
  const { isSystemOwner, isLoading: userLoading } = useCurrentUser();
  const tCommon = useT("common");

  // Mientras no sabemos el rol, nada: nunca parpadear contenido owner-only
  // antes de saber si el usuario puede verlo.
  if (userLoading) {
    return (
      <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Spinner />
          {tCommon("loading")}
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
  const t = useT("cortexMind");

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

  // C4 (investigación 2026-07-11): el diario — la capa narrativa que faltaba
  // sobre la vida interior (narrativas versionadas + reflexiones/aprendizajes).
  const journalQuery = useQuery<CortexJournalEntry[], ApiError>({
    queryKey: ["cortex", "journal"],
    queryFn: () => cortexFetch<CortexJournalEntry[]>("/journal?limit=50"),
    refetchOnWindowFocus: false,
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
            {t("loadError", {
              detail:
                mindQuery.error instanceof ApiError
                  ? mindQuery.error.body
                  : String(mindQuery.error),
            })}
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

      {/* Diales PAD + drives + copy honesto OBLIGATORIO (ADR 0075 §6). Los tres
          viven juntos en `MindPanel` para que no exista forma de montar los
          diales sin el aviso, ni aquí ni en la segunda columna del chat. */}
      <div className="mt-4">
        <MindPanel mind={mind} />
      </div>

      {/* Espacio PAD 2D con estela: las dos dimensiones del cuadrante a la vez
          (la línea de abajo sólo enseña la valencia del mood en el tiempo). */}
      <div className="mt-6">
        <MindPadSpace
          current={
            mind
              ? { valence: mind.valence, arousal: mind.arousal, mood_label: mind.mood_label }
              : null
          }
          snapshots={timeseriesQuery.data ?? []}
          isLoading={timeseriesQuery.isLoading}
          isError={timeseriesQuery.isError}
        />
      </div>

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
        <JournalPanel
          entries={journalQuery.data ?? []}
          isLoading={journalQuery.isLoading}
          isError={journalQuery.isError}
        />
      </div>

      <div className="mt-6">
        <AutonomyPanel />
      </div>

      <div className="mt-6">
        <BrowseInboxPanel />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lo que está aprendiendo — historial de curiosidad (ADR 0078, copy honesto)
//
// El panel (con su gate de aprobación y su copy bilingüe) vive en
// `components/cortex/learning-panel.tsx` para poder testearlo aislado; aquí sólo
// se monta. Las etiquetas de estado ya no son un `const` inline sin prueba: son
// los helpers puros de `lib/cortex-curiosity.ts`.
// ---------------------------------------------------------------------------

function MindHeader() {
  const t = useT("cortexMind");
  return (
    <PageHeader
      icon={<Activity className="h-6 w-6 sm:h-7 sm:w-7" />}
      title={t("title")}
      description={t("description")}
      data-testid="cortex-mind-header"
    />
  );
}

// ---------------------------------------------------------------------------
// El banner de honestidad YA NO VIVE AQUÍ (ADR 0075 §6).
//
// Se lo llevó `components/cortex/mind-panel.tsx` junto con los diales y los
// drives, y no es un movimiento cosmético: mientras el aviso y los diales eran
// piezas separadas que la pantalla montaba una al lado de la otra, montar los
// diales SIN el aviso era un olvido de una línea. Ahora no hay forma de pedir
// los diales sin el aviso, aquí ni en la segunda columna del chat, porque son el
// mismo componente y su test lo afirma.
//
// El respaldo bilingüe sigue saliendo del diccionario
// (`cortexCuriosity.honestyFallback`) y su invariante lo cubre
// `honesty-i18n.test.tsx`, que renderiza ESTA pantalla — así que también
// acredita el traslado.
// ---------------------------------------------------------------------------

function CortexMindNoAccess() {
  const t = useT("cortexMind");
  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        data-testid="cortex-mind-header"
      />
      <EmptyState
        data-testid="cortex-mind-no-access"
        icon={Brain}
        title={t("noAccessTitle")}
        description={t("noAccessDescription")}
      />
    </div>
  );
}
