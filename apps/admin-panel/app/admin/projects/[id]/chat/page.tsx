"use client";

/**
 * task_03_05 — Selector de modo persistente en la cabecera del chat.
 *
 * Scaffold for the project chat surface. Loads the project's
 * conversations, picks the most recent one (or creates one on demand),
 * and exposes the chat-mode selector (Planning / Discusión / Ejecución
 * / Custom) in the header.
 *
 * Changing the active mode does a PUT /conversations/{id} with
 * `current_mode`, which the backend persists and announces via a
 * system "modo cambiado" message + a `conversation.mode_changed`
 * WebSocket event. The UI optimistically updates while the request is
 * in flight so the click feels instant; on failure we revert.
 *
 * This page only ships the *selector* in this task. The message feed,
 * @-mentions, etc. come from task_03_07..12.
 */

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessagesSquare } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

// --------------------------------------------------------------------------
// Types
// --------------------------------------------------------------------------
interface Conversation {
  id: string;
  tenant_id: string;
  project_id: string;
  title: string | null;
  current_mode: string;
  custom_mode_name: string | null;
  related_plan_id: string | null;
}

interface ModeOption {
  value: string;
  labelEs: string;
  labelEn: string;
  description: string;
}

// Built-in modes; ``custom`` is reachable via a separate creation flow
// in task_03_08, not from this base selector.
const BUILT_IN_MODES: ModeOption[] = [
  {
    value: "planning",
    labelEs: "Planning",
    labelEn: "Planning",
    description: "El equipo construye un plan estructurado",
  },
  {
    value: "discussion",
    labelEs: "Discusión",
    labelEn: "Discussion",
    description: "Ronda abierta de ideas y opiniones",
  },
  {
    value: "execution",
    labelEs: "Ejecución",
    labelEn: "Execution",
    description: "El equipo ejecuta tareas del plan aprobado",
  },
];

// --------------------------------------------------------------------------
// Mode selector — three pill buttons in a segmented control.
// --------------------------------------------------------------------------
interface ChatModeSelectorProps {
  current: string;
  pending: boolean;
  onChange: (next: string) => void;
}

export function ChatModeSelector({ current, pending, onChange }: ChatModeSelectorProps) {
  return (
    <div
      role="group"
      aria-label="Modo de chat"
      data-testid="chat-mode-selector"
      className="bg-muted inline-flex rounded-md p-0.5"
    >
      {BUILT_IN_MODES.map((mode) => {
        const active = mode.value === current;
        return (
          <button
            key={mode.value}
            type="button"
            disabled={pending}
            data-testid={`chat-mode-${mode.value}`}
            data-active={active ? "true" : "false"}
            aria-pressed={active}
            title={mode.description}
            onClick={() => {
              if (!active && !pending) onChange(mode.value);
            }}
            className={cn(
              "px-3 py-1.5 text-sm font-medium rounded transition-colors",
              active
                ? "bg-background text-foreground shadow"
                : "text-muted-foreground hover:text-foreground",
              pending && "cursor-wait opacity-60",
            )}
          >
            {mode.labelEs}
          </button>
        );
      })}
    </div>
  );
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function ProjectChatPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const queryClient = useQueryClient();
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);

  const conversationsQuery = useQuery({
    queryKey: ["conversations", projectId],
    queryFn: () => apiFetch<Conversation[]>(`/projects/${projectId}/conversations`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
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
    onSettled: () => {
      queryClient.invalidateQueries({
        queryKey: ["conversations", projectId],
      });
    },
  });

  // ----------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------
  if (conversationsQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <p className="text-muted-foreground text-sm">Cargando chat…</p>
      </div>
    );
  }

  if (conversationsQuery.isError) {
    const err = conversationsQuery.error;
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <Card>
          <CardHeader>
            <CardTitle>Error cargando conversaciones</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-destructive text-sm" data-testid="chat-error">
              {err instanceof ApiError ? err.body : String(err)}
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
        <Card>
          <CardHeader>
            <CardTitle>No hay conversaciones en este proyecto</CardTitle>
          </CardHeader>
          <CardContent>
            <Button
              data-testid="chat-create-conversation"
              onClick={() => createConversation.mutate()}
              disabled={createConversation.isPending}
            >
              Empezar una conversación
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<MessagesSquare className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Chat del proyecto"
        description={activeConversation?.title ?? "Conversación con el equipo del proyecto"}
        actions={
          activeConversation ? (
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
          ) : null
        }
        data-testid="chat-page-header"
      />

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>
            Modo activo:{" "}
            <span data-testid="chat-current-mode">{activeConversation?.current_mode}</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            Los mensajes en vivo llegan en task_03_11 (Fase C). De momento, esta pantalla expone el
            selector de modo y la conversación activa que será la base de las próximas iteraciones.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
