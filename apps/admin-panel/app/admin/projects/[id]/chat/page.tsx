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
import { chatRefetchInterval, isReplyInFlight } from "@/lib/chat-feed";
import { conversationLabel, nextActiveAfterDelete } from "@/lib/conversation-history";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { useErrorText } from "@/lib/use-error-text";
import { useWebSocket, wsUrl } from "@/lib/ws";

import { ChatComposer } from "./chat-composer";
import { ChatModeSelector } from "./chat-mode-selector";
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
    refetchInterval: (query) =>
      chatRefetchInterval(query.state.data as Message[] | undefined, Date.now()),
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
    onSuccess: (created) => {
      queryClient.setQueryData<Message[]>(["messages", created.conversation_id], (prev) =>
        prev ? [...prev, created] : [created],
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
          <MessageFeed messages={messagesQuery.data ?? []} loading={messagesQuery.isLoading} />
          {(() => {
            // "Pensando" mientras el turno sigue en vuelo: en planning abarca toda la ronda
            // (PM → especialistas → síntesis), no solo hasta el primer mensaje del equipo.
            const awaitingReply = isReplyInFlight(messagesQuery.data, Date.now());
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
              messages={messagesQuery.data ?? []}
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
