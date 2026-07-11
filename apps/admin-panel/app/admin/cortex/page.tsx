"use client";

/**
 * Córtex del System Owner — chat con hilo persistente (Córtex F1, Tarea 12).
 *
 * El córtex es la "mente útil mínima" del dueño del despliegue: hilo
 * persistente entre turnos (que el asistente de tenant NO tiene), recall
 * asociativo sobre su memoria privada y deliberación con razonamiento
 * profundo. Esta página clona la superficie de chat del asistente
 * (`app/admin/assistant/page.tsx`) pero:
 *
 *   (a) está gated por `isSystemOwner` (no `isTenantAdmin`); un no-owner ve
 *       `cortex-no-access` y NUNCA el input (el backend devuelve 403 — esto
 *       es solo UX, la barrera real es `require_system_owner`).
 *   (b) tiene HILO PERSISTENTE: al montar `GET /owner/cortex/conversations`,
 *       selector de hilo + "Nueva conversación" (patrón de historial de
 *       `projects/[id]/chat`); `POST /owner/cortex/turns` con `conversation_id`;
 *       los turnos se cargan con `GET /owner/cortex/turns`.
 *   (c) renderiza las respuestas del córtex con `renderPlanDraft` (markdown).
 *   (d) muestra un indicador "pensando" en vuelo, y "pensando a fondo" cuando
 *       el último turno usó razonamiento profundo (`reasoning_effort`).
 *
 * Honestidad de producto (riesgo F1): el córtex F1 es una mente SIMULADA con
 * memoria + deliberación; NO tiene afecto ni consciencia (eso llega en F2). El
 * copy de esta página no insinúa emociones.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Phone, Send } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { CortexVoiceCall } from "@/components/cortex/cortex-voice-call";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api";
import {
  CORTEX_LIMITS,
  cortexConversationLabel,
  cortexFetch,
  getCortexConversations,
  getCortexTurns,
  postCortexTurn,
  type CortexConversation,
  type CortexTurnItem,
  type CortexTurnResponse,
} from "@/lib/cortex";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/lib/use-current-user";

export default function CortexChatPage() {
  const { isSystemOwner, isLoading: userLoading } = useCurrentUser();
  const queryClient = useQueryClient();

  const [draft, setDraft] = useState("");
  // Tri-estado del hilo activo: `undefined` = aún sin decidir (el efecto de
  // auto-selección puede elegir el más reciente al cargar); `null` = el owner
  // pulsó «Nueva conversación» (elección EXPLÍCITA — el auto-select no la pisa).
  const [activeConversationId, setActiveConversationId] = useState<string | null | undefined>(
    undefined,
  );
  const [forbidden, setForbidden] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  // El effort efectivo del último turno del córtex; alimenta el indicador de
  // "pensando a fondo" del siguiente turno.
  const [lastEffort, setLastEffort] = useState<string | null>(null);
  // Eco optimista: el mensaje recién enviado, visible como burbuja MIENTRAS el
  // córtex delibera (el POST es síncrono y puede tardar). Se oculta solo cuando
  // el refetch del hilo ya lo trae persistido (sin flicker ni duplicado).
  const [pendingEcho, setPendingEcho] = useState<string | null>(null);

  // C12 (investigación 2026-07-11): el mood vivo también en el CHAT — en voz ya
  // se materializa (prosodia + avatar) pero en texto el owner no lo veía. Solo
  // lectura ligera del Panel de Mente, refrescada con calma.
  const mindQuery = useQuery<{ mood_label?: string }, ApiError>({
    queryKey: ["cortex-mind-chat"],
    queryFn: () => cortexFetch<{ mood_label?: string }>("/mind"),
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const moodLabel = mindQuery.data?.mood_label?.trim();

  const conversationsQuery = useQuery<CortexConversation[], ApiError>({
    queryKey: ["cortex", "conversations"],
    queryFn: getCortexConversations,
    enabled: isSystemOwner,
    refetchOnWindowFocus: false,
    retry: false,
  });

  // Auto-selecciona el hilo más reciente en cuanto llega la lista — SOLO si el
  // owner aún no ha decidido (undefined). `null` es «Nueva conversación»
  // explícita y el auto-select NO debe pisarla (bug QA 2026-07-06).
  useEffect(() => {
    if (activeConversationId !== undefined) return;
    const conversations = conversationsQuery.data;
    if (conversations && conversations.length > 0) {
      setActiveConversationId(conversations[0].id);
    }
  }, [activeConversationId, conversationsQuery.data]);

  const turnsQuery = useQuery<CortexTurnItem[], ApiError>({
    queryKey: ["cortex", "turns", activeConversationId],
    queryFn: () => getCortexTurns(activeConversationId as string),
    enabled: isSystemOwner && Boolean(activeConversationId),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const mutation = useMutation<CortexTurnResponse, ApiError, string>({
    mutationFn: (message) =>
      postCortexTurn({
        message,
        conversation_id: activeConversationId ?? undefined,
      }),
    onSuccess: (data) => {
      setLastEffort(data.reasoning_effort ?? null);
      // Un primer turno crea el hilo: adóptalo como activo y refresca la lista.
      if (!activeConversationId) {
        setActiveConversationId(data.conversation_id);
      }
      queryClient.invalidateQueries({ queryKey: ["cortex", "conversations"] });
      queryClient.invalidateQueries({
        queryKey: ["cortex", "turns", data.conversation_id],
      });
    },
    onError: (error, message) => {
      // El envío falló: retira el eco y devuelve el texto al input para que el
      // owner no pierda lo que escribió.
      setPendingEcho(null);
      setDraft((current) => current || message);
      // Un 403 aquí significa que dejaste de ser owner tras cargar: refleja el
      // gate del backend en vez de mostrar un chat que sabemos denegado.
      if (error.status === 403) setForbidden(true);
    },
  });

  // Mientras no sabemos el rol, nada interactivo: nunca parpadear el input
  // antes de saber si el usuario puede usarlo.
  if (userLoading) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Spinner />
          Cargando…
        </p>
      </div>
    );
  }

  // No-owner / 403 -> sin input (e2e: count 0). El backend es la barrera real.
  if (!isSystemOwner || forbidden) {
    return <CortexNoAccess />;
  }

  const trimmed = draft.trim();
  const canSend = trimmed.length > 0 && !mutation.isPending;

  const submit = () => {
    if (!canSend) return;
    const message = trimmed.slice(0, CORTEX_LIMITS.message.max);
    setDraft("");
    setPendingEcho(message);
    mutation.mutate(message);
  };

  const conversations = conversationsQuery.data ?? [];
  const turns = turnsQuery.data ?? [];
  // El eco optimista se muestra hasta que el refetch trae el turno persistido
  // con el mismo contenido (entonces desaparece sin duplicarse ni parpadear).
  const echoVisible =
    pendingEcho !== null && !turns.some((t) => t.role === "user" && t.content === pendingEcho);
  // El effort se considera "profundo" cuando no es off/ninguno.
  const deepThinking = lastEffort !== null && lastEffort !== "off" && lastEffort !== "none";

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Córtex"
        description="Tu córtex con hilo persistente, recall asociativo sobre tu memoria privada y deliberación profunda. Es una mente simulada y útil — sin afecto ni consciencia."
        actions={
          <div className="flex items-center gap-2">
            {moodLabel ? (
              <span
                className="text-muted-foreground rounded-full border px-2 py-0.5 text-xs"
                title="Estado afectivo simulado (modelo computacional, ADR 0075)"
                data-testid="cortex-chat-mood"
              >
                ánimo: {moodLabel} (simulado)
              </span>
            ) : null}
            <Button
              variant={voiceMode ? "default" : "outline"}
              onClick={() => setVoiceMode((v) => !v)}
              data-testid="cortex-voice-toggle"
            >
              <Phone className="mr-2 h-4 w-4" />
              {voiceMode ? "Cerrar voz" : "Modo voz"}
            </Button>
          </div>
        }
        data-testid="cortex-chat-header"
      />

      {/* Videollamada a pantalla completa; el copy de honestidad (afecto
          simulado, ADR 0075) viaja en el subtítulo y la etiqueta de mood. */}
      {voiceMode ? <CortexVoiceCall onClose={() => setVoiceMode(false)} /> : null}

      {/* Historial de hilos: cambia entre conversaciones o empieza una nueva
          sin borrar las demás (patrón del chat de proyecto). */}
      <div className="mt-4 flex flex-wrap items-center gap-2" data-testid="cortex-history-bar">
        <label htmlFor="cortex-conversation-picker" className="text-muted-foreground text-sm">
          Hilo:
        </label>
        <div className="w-full min-w-0 sm:w-72">
          <Select
            id="cortex-conversation-picker"
            value={activeConversationId ?? ""}
            onChange={(e) => {
              setActiveConversationId(e.target.value || null);
              setPendingEcho(null);
            }}
            data-testid="cortex-conversation-picker"
            disabled={conversations.length === 0}
          >
            {conversations.length === 0 ? (
              <option value="">Sin hilos todavía</option>
            ) : (
              conversations.map((c) => (
                <option key={c.id} value={c.id}>
                  {cortexConversationLabel(c)}
                </option>
              ))
            )}
          </Select>
        </div>
        <Button
          variant="outline"
          size="sm"
          data-testid="cortex-conversation-new"
          disabled={mutation.isPending}
          onClick={() => {
            // "Nueva conversación" = desasociar el hilo activo (null EXPLÍCITO,
            // el auto-select no lo pisa); el próximo turno crea uno nuevo en el
            // backend y lo adopta como activo.
            setActiveConversationId(null);
            setLastEffort(null);
            setDraft("");
            setPendingEcho(null);
          }}
        >
          Nueva conversación
        </Button>
      </div>

      <Card className="mt-6">
        <CardContent className="flex flex-col gap-4 pt-5">
          <div
            data-testid="cortex-chat"
            className="flex min-h-[16rem] flex-col gap-3"
            aria-live="polite"
          >
            {turnsQuery.isLoading && activeConversationId ? (
              <p className="text-muted-foreground flex items-center gap-2 text-sm">
                <Spinner />
                Cargando turnos…
              </p>
            ) : turns.length === 0 && !mutation.isPending && !echoVisible ? (
              <EmptyState
                icon={Brain}
                title="Empieza a pensar en voz alta"
                description="Pregunta cualquier cosa; el córtex recuerda este hilo y lo que le has contado antes."
              />
            ) : (
              turns.map((turn) => <CortexBubble key={turn.id} turn={turn} />)
            )}
            {echoVisible ? (
              <CortexBubble
                turn={{
                  id: "pending-echo",
                  role: "user",
                  content: pendingEcho as string,
                  created_at: "",
                }}
              />
            ) : null}
            {mutation.isPending ? (
              <p
                className="text-muted-foreground flex items-center gap-2 text-sm"
                data-testid="cortex-thinking"
              >
                <Spinner />
                {deepThinking ? "Pensando a fondo…" : "Pensando…"}
              </p>
            ) : null}
          </div>

          {mutation.isError && !forbidden ? (
            <p className="text-destructive text-sm" data-testid="cortex-chat-error">
              {mutation.error instanceof ApiError ? mutation.error.body : String(mutation.error)}
            </p>
          ) : null}

          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <input
              data-testid="cortex-input"
              aria-label="Mensaje para el córtex"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={CORTEX_LIMITS.message.max}
              placeholder="Escribe tu mensaje…"
              className="border-input bg-background placeholder:text-muted-foreground focus-visible:ring-ring focus-visible:ring-offset-background flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
            />
            <Button type="submit" data-testid="cortex-send" disabled={!canSend}>
              <Send className="mr-2 h-4 w-4" />
              Enviar
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function CortexBubble({ turn }: { turn: CortexTurnItem }) {
  const isUser = turn.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        data-testid={isUser ? "cortex-question" : "cortex-answer"}
        className={cn(
          "max-w-[85%] rounded-lg px-3 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted text-foreground border",
        )}
      >
        {isUser ? (
          // El texto del owner queda verbatim (nunca se renderiza como markdown).
          <p className="whitespace-pre-wrap">{turn.content}</p>
        ) : (
          // Las respuestas del córtex (tablas, listas, encabezados) se renderizan
          // como markdown con el renderer XSS-safe compartido.
          renderPlanDraft(turn.content)
        )}
        {!isUser && turn.model_id ? (
          <p className="text-muted-foreground mt-1 text-[10px] uppercase tracking-wide">
            {turn.model_id}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function CortexNoAccess() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Córtex"
        data-testid="cortex-chat-header"
      />
      <EmptyState
        data-testid="cortex-no-access"
        icon={Brain}
        title="Córtex no disponible"
        description="El córtex es exclusivo del System Owner (el dueño del despliegue). Tu cuenta no tiene ese rol."
      />
    </div>
  );
}
