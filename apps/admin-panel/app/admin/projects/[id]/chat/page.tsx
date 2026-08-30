"use client";

/**
 * Chat del proyecto — pantalla (task_03_05 y siguientes).
 *
 * Carga las conversaciones del proyecto, elige la más reciente (o crea una a
 * demanda) y monta la cabecera con el selector de modo (Planning / Discusión /
 * Ejecución / Custom).
 *
 * Cambiar el modo activo hace `PUT /conversations/{id}` con `current_mode`, que
 * el backend persiste y anuncia con un mensaje de sistema "modo cambiado" + un
 * evento WebSocket `conversation.mode_changed`. La UI se actualiza de forma
 * optimista mientras la petición vuela para que el clic se sienta instantáneo;
 * si falla, revierte.
 *
 * **Troceada en prod-16 `task_prod16_08`** (926 líneas): esta pantalla se queda
 * con el ESTADO —queries, mutaciones, WebSocket y composición— y las piezas
 * viven al lado, colocadas:
 *
 *   · `chat-types.ts`          — `Conversation`/`Message` y los modos incorporados
 *   · `chat-mode-selector.tsx` — el control segmentado de la cabecera
 *   · `message-feed.tsx`       — la lista, la fila y el resumen plegable
 *   · `generate-plan-button.tsx` — el CTA que materializa el plan
 *   · `chat-composer.tsx`      — el textarea con @-menciones y vista previa
 *   · `chat-echo.ts`           — el eco optimista y su reconciliación (H7)
 *   · `chat-turn.ts`           — si el equipo sigue trabajando en el turno (H8)
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessagesSquare } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Select } from "@/components/ui/select";
import { apiFetch } from "@/lib/api";
import { conversationLabel, nextActiveAfterDelete } from "@/lib/conversation-history";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { useErrorText } from "@/lib/use-error-text";
import { useWebSocket, wsUrl } from "@/lib/ws";

import { ChatComposer } from "./chat-composer";
import { appendUnlessPresent, mergePendingEchoes, nextEchoId, type PendingEcho } from "./chat-echo";
import { ChatModeSelector } from "./chat-mode-selector";
import { chatPollInterval, isTeamWorking, turnFeed } from "./chat-turn";
import { MESSAGE_WINDOW, type Conversation, type Message } from "./chat-types";
import { GeneratePlanButton } from "./generate-plan-button";
import { MessageFeed } from "./message-feed";

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectChatPage() {
  const t = useT("projectChat");
  const lang = useLangOptional();
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const queryClient = useQueryClient();
  const errorText = useErrorText();
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [confirmClearOpen, setConfirmClearOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  // Mensajes enviados que aún no han vuelto del servidor (H7). Fuera de la caché
  // de React Query a propósito: el feed se re-sondea cada 3 s mientras el turno
  // está en vuelo y un refetch reemplaza el array entero, llevándose por delante
  // un eco sin confirmar. Ver `chat-echo.ts`.
  const [pendingEchoes, setPendingEchoes] = useState<PendingEcho[]>([]);

  const conversationsQuery = useQuery({
    queryKey: ["conversations", projectId],
    queryFn: () => apiFetch<Conversation[]>(`/projects/${projectId}/conversations`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  // task_wf_43: a quién se puede @-mencionar EN ESTE proyecto. La lista salía
  // del enum completo, así que ofrecía especialistas que el equipo no tiene: la
  // mención se enviaba, el servidor la descartaba por no estar en el equipo y el
  // turno quedaba vacío. La composición del equipo cambia poco, de ahí el
  // staleTime largo.
  const planningRolesQuery = useQuery({
    queryKey: ["planning-roles", projectId],
    queryFn: () => apiFetch<{ roles: string[] }>(`/projects/${projectId}/planning-roles`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
    staleTime: 5 * 60_000,
  });

  // Auto-select the most recent conversation as soon as the list lands.
  useEffect(() => {
    if (activeConversationId) return;
    const conversations = conversationsQuery.data;
    if (conversations && conversations.length > 0) {
      setActiveConversationId(conversations[conversations.length - 1].id);
    }
  }, [activeConversationId, conversationsQuery.data]);

  const activeConversation = useMemo(() => {
    if (!activeConversationId || !conversationsQuery.data) return null;
    return conversationsQuery.data.find((c) => c.id === activeConversationId) ?? null;
  }, [activeConversationId, conversationsQuery.data]);

  const createConversation = useMutation({
    mutationFn: () =>
      apiFetch<Conversation>(`/projects/${projectId}/conversations`, {
        method: "POST",
        body: { title: null, current_mode: "planning" },
      }),
    onSuccess: (created) => {
      queryClient.setQueryData<Conversation[]>(["conversations", projectId], (prev) =>
        prev ? [...prev, created] : [created],
      );
      setActiveConversationId(created.id);
    },
  });

  const updateMode = useMutation({
    mutationFn: async ({ conversationId, mode }: { conversationId: string; mode: string }) =>
      apiFetch<Conversation>(`/conversations/${conversationId}`, {
        method: "PUT",
        body: { current_mode: mode },
      }),
    onMutate: async ({ conversationId, mode }) => {
      await queryClient.cancelQueries({
        queryKey: ["conversations", projectId],
      });
      const prev = queryClient.getQueryData<Conversation[]>(["conversations", projectId]);
      queryClient.setQueryData<Conversation[]>(["conversations", projectId], (current) =>
        current
          ? current.map((c) => (c.id === conversationId ? { ...c, current_mode: mode } : c))
          : current,
      );
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) {
        queryClient.setQueryData(["conversations", projectId], ctx.prev);
      }
    },
    onSettled: (_data, _err, vars) => {
      queryClient.invalidateQueries({
        queryKey: ["conversations", projectId],
      });
      // The mode change posts a `system` "modo cambiado" message
      // server-side. Refetch the feed so the banner appears without
      // waiting for the WebSocket round-trip.
      queryClient.invalidateQueries({
        queryKey: ["messages", vars.conversationId],
      });
    },
  });

  // Message feed for the active conversation. Disabled until we know
  // which conversation to load; the chat-mode-selector test never
  // exercises this query because it doesn't mock /messages.
  const messagesQuery = useQuery({
    queryKey: ["messages", activeConversationId],
    // A-01: límite EXPLÍCITO. El endpoint devuelve los más RECIENTES (antes daba
    // los primeros N, y pasada la ventana el feed se congelaba en el arranque de
    // la conversación y «Generar Plan» desaparecía). Para releer lo anterior está
    // `?before=<id>`, que este feed aún no usa: la ventana cubre de sobra un turno.
    queryFn: () =>
      apiFetch<Message[]>(
        `/conversations/${activeConversationId}/messages?limit=${MESSAGE_WINDOW}`,
      ),
    refetchOnWindowFocus: false,
    enabled: Boolean(activeConversationId),
    // Safety net for live updates: the WebSocket below pushes each step in real time, but if
    // it never connected or got dropped (proxy idle-timeout, laptop sleep/wake), poll while the
    // turn is still in flight so the reply lands without a manual reload. A PLANNING turn emits
    // many messages over minutes (PM → specialists → synthesis), so `isReplyInFlight` keeps
    // polling for the whole turn — not just until the first agent message — then stops (no idle
    // polling). See lib/chat-feed.
    // H8: `chatPollInterval` es `chatRefetchInterval` menos los turnos que un
    // aviso del sistema ya cerró — sondear tres minutos algo que nadie va a
    // escribir es la otra mitad del mismo error que el spinner fantasma.
    refetchInterval: (query) =>
      chatPollInterval(query.state.data as Message[] | undefined, Date.now()),
  });

  // POST a new user message. The composer below uses this; the
  // @-mention chips are part of the content string itself (no
  // separate field) so the backend's existing schema accepts them
  // without changes.
  const postMessage = useMutation({
    mutationFn: async ({ conversationId, content }: { conversationId: string; content: string }) =>
      apiFetch<Message>(`/conversations/${conversationId}/messages`, {
        method: "POST",
        body: { author_kind: "user", content },
      }),
    // H7 (a): el usuario ve su mensaje al pulsar «Enviar», sin esperar al ida y
    // vuelta. `seenIds` congela lo que ya había en el feed para poder reconocer
    // después CUÁL de los mensajes de usuario es el suyo — repetir la misma
    // frase es legítimo y casar por texto a secas se comería el segundo.
    onMutate: ({ conversationId, content }) => {
      const echo: PendingEcho = {
        tempId: nextEchoId(),
        conversationId,
        content,
        mode: activeConversation?.current_mode ?? "",
        createdAt: new Date().toISOString(),
        seenIds: (queryClient.getQueryData<Message[]>(["messages", conversationId]) ?? []).map(
          (m) => m.id,
        ),
      };
      setPendingEchoes((prev) => [...prev, echo]);
      return { tempId: echo.tempId };
    },
    // H7 (b): el WebSocket publica el mensaje ANTES de que el POST conteste
    // (`routers/conversations.py`), así que aquí llegaba un id que ya estaba en
    // la caché y se añadía otra vez. De ahí los dos globos `USER · PLANNING`.
    onSuccess: (created, _vars, ctx) => {
      queryClient.setQueryData<Message[]>(["messages", created.conversation_id], (prev) =>
        appendUnlessPresent(prev, created),
      );
      // El eco se retira SÓLO al salir bien: el persistido ya ocupa su sitio.
      // React agrupa esta actualización con la de arriba, así que no parpadea.
      const tempId = ctx?.tempId;
      if (tempId) setPendingEchoes((prev) => prev.filter((e) => e.tempId !== tempId));
    },
    // Y si falla, el eco se QUEDA. Antes se retiraba en `onSettled` —o sea,
    // también al fallar— y no había `onError`: el mensaje que el usuario acababa
    // de escribir desaparecía de la pantalla sin que nada dijese por qué. El
    // eco es el único sitio donde queda ese texto, así que pasa a fallido: sigue
    // visible, deja de decir «enviando…» (que ya no es cierto) y ofrece
    // reintentar.
    onError: (_error, _vars, ctx) => {
      const tempId = ctx?.tempId;
      if (!tempId) return;
      setPendingEchoes((prev) =>
        prev.map((e) => (e.tempId === tempId ? { ...e, failed: true } : e)),
      );
    },
  });

  // Empty the chat: clears the conversation's messages (keeps the conversation) so
  // history doesn't pile up and the next turn starts with fresh context.
  const clearMessages = useMutation({
    mutationFn: async (conversationId: string) =>
      apiFetch<void>(`/conversations/${conversationId}/messages`, { method: "DELETE" }),
    onSuccess: (_result, conversationId) => {
      queryClient.setQueryData<Message[]>(["messages", conversationId], []);
    },
  });

  // Delete a whole conversation from the history (hard-deletes its messages +
  // Redis stream server-side). After deleting, jump to the most recent remaining
  // conversation (or fall back to the empty state when none are left).
  const deleteConversation = useMutation({
    mutationFn: async (conversationId: string) =>
      apiFetch<void>(`/conversations/${conversationId}`, { method: "DELETE" }),
    onSuccess: (_result, conversationId) => {
      const current = queryClient.getQueryData<Conversation[]>(["conversations", projectId]) ?? [];
      const nextActive = nextActiveAfterDelete(current, conversationId);
      queryClient.setQueryData<Conversation[]>(
        ["conversations", projectId],
        current.filter((c) => c.id !== conversationId),
      );
      queryClient.removeQueries({ queryKey: ["messages", conversationId] });
      setActiveConversationId(nextActive);
    },
  });

  // Live updates: the team's reply streams in message-by-message over the
  // per-conversation WebSocket (the responder publishes each step). Without this
  // the feed only refreshed on reload — the chat looked "hung" while waiting.
  useWebSocket(
    activeConversationId ? wsUrl(`/ws/conversation/${activeConversationId}`) : null,
    (data: unknown) => {
      const frame = data as { type?: string; payload?: Record<string, unknown> | null };
      if (frame?.type !== "message.created" || !frame.payload || !activeConversationId) return;
      const p = frame.payload;
      const id = String(p.message_id ?? "");
      if (!id) return;
      queryClient.setQueryData<Message[]>(["messages", activeConversationId], (prev) => {
        if (prev?.some((m) => m.id === id)) return prev; // dedup optimistic / echo
        const msg: Message = {
          id,
          tenant_id: "",
          conversation_id: activeConversationId,
          author_kind: (p.author_kind as Message["author_kind"]) ?? "agent",
          author_user_id: (p.author_user_id as string | null) ?? null,
          author_agent_id: (p.author_agent_id as string | null) ?? null,
          content: String(p.content ?? ""),
          mode: String(p.mode ?? ""),
          attachments: (p.attachments as Array<Record<string, unknown>>) ?? [],
          related_plan_id: null,
          is_summary: Boolean(p.is_summary),
          created_at: new Date().toISOString(),
        };
        return prev ? [...prev, msg] : [msg];
      });
    },
  );

  // El feed a pintar: lo persistido más los ecos de ESTA conversación que aún no
  // han vuelto (H7). El filtro por conversación no es decorativo: sin él, enviar
  // y cambiar de hilo pintaría el eco en el hilo equivocado.
  const feed = useMemo(
    () =>
      mergePendingEchoes(
        messagesQuery.data ?? [],
        pendingEchoes.filter((e) => e.conversationId === activeConversationId),
      ),
    [messagesQuery.data, pendingEchoes, activeConversationId],
  );
  // Los ecos se pintan marcados como «enviando…»: un eco que se hace pasar por
  // mensaje ya entregado es una mentira pequeña, pero es la que hace dudar al
  // usuario de si pulsó dos veces. Los que fallaron salen de este conjunto y
  // entran en el de abajo: seguir diciendo «enviando…» de algo que ya terminó
  // (y mal) es la misma mentira, más cara.
  const pendingIds = useMemo(
    () => new Set(pendingEchoes.filter((e) => !e.failed).map((e) => e.tempId)),
    [pendingEchoes],
  );
  const failedIds = useMemo(
    () => new Set(pendingEchoes.filter((e) => e.failed).map((e) => e.tempId)),
    [pendingEchoes],
  );
  // El aviso de fallo se ata al hilo ABIERTO por el mismo motivo que el eco: un
  // error de otra conversación pintado aquí acusa al mensaje equivocado.
  const failedHere = pendingEchoes.some(
    (e) => e.failed && e.conversationId === activeConversationId,
  );

  // Reintentar un envío fallido: se retira el eco fallido y se vuelve a enviar
  // el MISMO texto, que es el que sigue en pantalla.
  const retrySend = (message: Message) => {
    const echo = pendingEchoes.find((e) => e.tempId === message.id);
    if (!echo) return;
    setPendingEchoes((prev) => prev.filter((e) => e.tempId !== echo.tempId));
    postMessage.mutate({ conversationId: echo.conversationId, content: echo.content });
  };

  // ----------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------
  if (conversationsQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <ProjectBreadcrumb projectId={projectId} current={t("breadcrumbCurrent")} />
        <p className="text-muted-foreground text-sm">{t("loading")}</p>
      </div>
    );
  }

  if (conversationsQuery.isError) {
    const err = conversationsQuery.error;
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <ProjectBreadcrumb projectId={projectId} current={t("breadcrumbCurrent")} />
        <Card>
          <CardHeader>
            <CardTitle>{t("errorTitle")}</CardTitle>
          </CardHeader>
          <CardContent>
            {/* prod-16 `task_prod16_05`: aquí se pintaba `err.body` CRUDO. */}
            <p className="text-destructive text-sm" data-testid="chat-error">
              {errorText(err)}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const conversations = conversationsQuery.data ?? [];
  if (conversations.length === 0) {
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <ProjectBreadcrumb projectId={projectId} current={t("breadcrumbCurrent")} />
        <Card>
          <CardHeader>
            <CardTitle>{t("noConversationsTitle")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Button
              data-testid="chat-create-conversation"
              onClick={() => createConversation.mutate()}
              disabled={createConversation.isPending}
            >
              {t("startConversation")}
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <ProjectBreadcrumb projectId={projectId} current={t("breadcrumbCurrent")} />
      <PageHeader
        icon={<MessagesSquare className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={activeConversation?.title ?? t("defaultDescription")}
        actions={
          activeConversation ? (
            <div className="flex items-center gap-2">
              <ChatModeSelector
                current={activeConversation.current_mode}
                pending={updateMode.isPending}
                onChange={(next) =>
                  updateMode.mutate({
                    conversationId: activeConversation.id,
                    mode: next,
                  })
                }
              />
              <Button
                variant="outline"
                size="sm"
                data-testid="chat-clear"
                disabled={clearMessages.isPending}
                onClick={() => setConfirmClearOpen(true)}
              >
                {t("clearChat")}
              </Button>
            </div>
          ) : null
        }
        data-testid="chat-page-header"
      />

      {/* Conversation history: switch between past conversations, start a new one
          without deleting the others, or delete one from the history. */}
      <div
        className="mt-4 flex flex-wrap items-center gap-2"
        data-testid="conversation-history-bar"
      >
        <label htmlFor="conversation-picker" className="text-muted-foreground text-sm">
          {t("conversationPickerLabel")}
        </label>
        <div className="w-full min-w-0 sm:w-72">
          <Select
            id="conversation-picker"
            value={activeConversationId ?? ""}
            onChange={(e) => setActiveConversationId(e.target.value)}
            data-testid="conversation-picker"
          >
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {conversationLabel(c, lang)} · {c.current_mode}
              </option>
            ))}
          </Select>
        </div>
        <Button
          variant="outline"
          size="sm"
          data-testid="conversation-new"
          disabled={createConversation.isPending}
          onClick={() => createConversation.mutate()}
        >
          {t("newConversation")}
        </Button>
        {activeConversation ? (
          <Button
            variant="outline"
            size="sm"
            data-testid="conversation-delete"
            disabled={deleteConversation.isPending}
            onClick={() => setConfirmDeleteOpen(true)}
          >
            {t("deleteConversation")}
          </Button>
        ) : null}
      </div>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>
            {t("activeMode")}{" "}
            <span data-testid="chat-current-mode">{activeConversation?.current_mode}</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <MessageFeed
            messages={feed}
            loading={messagesQuery.isLoading}
            pendingIds={pendingIds}
            failedIds={failedIds}
            onRetry={retrySend}
          />
          {/* El fallo, dicho con todas las letras. Sin esto el único indicio
              era la ausencia del mensaje, que es justo lo que no se ve. */}
          {postMessage.isError && failedHere ? (
            <p className="text-destructive mt-3 text-sm" role="alert" data-testid="chat-send-error">
              {t("sendErrorPrefix")} {errorText(postMessage.error)}
            </p>
          ) : null}
          {(() => {
            // "Pensando" mientras el turno sigue en vuelo: en planning abarca toda la ronda
            // (PM → especialistas → síntesis), no solo hasta el primer mensaje del equipo.
            //
            // H8: `isTeamWorking` es `isReplyInFlight` menos los turnos que un aviso del
            // sistema ya cerró. El aviso «el equipo no tiene agentes configurados» es un
            // mensaje `system` reciente y sin adjuntos, o sea que el propio mensaje que
            // declaraba que nadie iba a contestar encendía este indicador.
            //
            // Y `turnFeed` retira los ecos que FALLARON: se quedan pintados (es donde
            // sobrevive el texto del usuario), pero un mensaje que no llegó al servidor
            // no empieza ningún turno. Sin esto, el arreglo de H7 reintroducía H8 por
            // otra puerta — «no se pudo enviar» y «el equipo está pensando…» a la vez.
            const awaitingReply = isTeamWorking(turnFeed(feed, failedIds), Date.now());
            return awaitingReply ? (
              <p
                className="text-muted-foreground mt-3 animate-pulse text-sm"
                data-testid="chat-team-thinking"
              >
                {t("thinking")} <span className="opacity-60">{t("thinkingHint")}</span>
              </p>
            ) : null;
          })()}
          {activeConversation ? (
            <GeneratePlanButton
              messages={feed}
              projectId={projectId}
              conversationId={activeConversation.id}
            />
          ) : null}
          {activeConversation ? (
            <ChatComposer
              disabled={postMessage.isPending}
              roles={planningRolesQuery.data?.roles ?? []}
              onSubmit={(content) =>
                postMessage.mutate({
                  conversationId: activeConversation.id,
                  content,
                })
              }
            />
          ) : null}
        </CardContent>
      </Card>

      {activeConversation ? (
        <ConfirmDialog
          open={confirmClearOpen}
          onOpenChange={setConfirmClearOpen}
          title={t("clearChat")}
          description={t("confirmClearDescription")}
          confirmLabel={t("confirmClearLabel")}
          destructive
          pending={clearMessages.isPending}
          onConfirm={() =>
            clearMessages.mutate(activeConversation.id, {
              onSuccess: () => setConfirmClearOpen(false),
            })
          }
        />
      ) : null}

      {activeConversation ? (
        <ConfirmDialog
          open={confirmDeleteOpen}
          onOpenChange={setConfirmDeleteOpen}
          title={t("deleteConversation")}
          description={t("confirmDeleteDescription")}
          confirmLabel={t("confirmDeleteLabel")}
          destructive
          pending={deleteConversation.isPending}
          onConfirm={() =>
            deleteConversation.mutate(activeConversation.id, {
              onSuccess: () => setConfirmDeleteOpen(false),
            })
          }
        />
      ) : null}
    </div>
  );
}
