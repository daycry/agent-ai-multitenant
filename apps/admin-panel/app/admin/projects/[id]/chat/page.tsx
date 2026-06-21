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
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessagesSquare } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { renderPlanDraft } from "@/lib/plan-draft-md";
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

interface Message {
  id: string;
  tenant_id: string;
  conversation_id: string;
  author_kind: "user" | "agent" | "system";
  author_user_id: string | null;
  author_agent_id: string | null;
  content: string;
  mode: string;
  attachments: Array<Record<string, unknown>>;
  related_plan_id: string | null;
  is_summary: boolean;
  created_at: string;
}

// Built-in PlanningRoles mirrored from
// `api_server.chat.planning_graph.PlanningRole`. Used by the @-mention
// autocomplete (task_03_12) — the operator can address a specific
// specialist directly from the chat composer.
const PLANNING_ROLES = [
  "project_manager",
  "architect",
  "backend_dev",
  "frontend_dev",
  "qa",
  "reviewer",
  "devops",
  "security",
  "technical_writer",
] as const;

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
    queryFn: () => apiFetch<Message[]>(`/conversations/${activeConversationId}/messages`),
    refetchOnWindowFocus: false,
    enabled: Boolean(activeConversationId),
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

  // ----------------------------------------------------------------
  // Render
  // ----------------------------------------------------------------
  if (conversationsQuery.isLoading) {
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <ProjectBreadcrumb projectId={projectId} current="Chat" />
        <p className="text-muted-foreground text-sm">Cargando chat…</p>
      </div>
    );
  }

  if (conversationsQuery.isError) {
    const err = conversationsQuery.error;
    return (
      <div className="mx-auto w-full max-w-7xl px-4 py-8">
        <ProjectBreadcrumb projectId={projectId} current="Chat" />
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
        <ProjectBreadcrumb projectId={projectId} current="Chat" />
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
      <ProjectBreadcrumb projectId={projectId} current="Chat" />
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
          <MessageFeed messages={messagesQuery.data ?? []} loading={messagesQuery.isLoading} />
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
    </div>
  );
}

// --------------------------------------------------------------------------
// "Generar Plan" button (task_03_13)
//
// Visibility rule: only shown when the LAST agent message in the feed
// carries an attachment of the shape
//   {"kind": "planning_directive", "intent": "finish_planning"}.
// That attachment is appended by the chat endpoint (Fase G wiring)
// whenever the planning sub-graph returns PMIntent.FINISH_PLANNING.
//
// Clicking the button POSTs `/projects/{id}/plans` with the
// conversation id — the backend (task_03_14) materialises the
// canonical-template Plan row from the chat history. We optimistic-
// disable the button while the POST is in flight and surface any
// error inline.
// --------------------------------------------------------------------------
interface GeneratePlanButtonProps {
  messages: Message[];
  projectId: string;
  conversationId: string;
}

function GeneratePlanButton({ messages, projectId, conversationId }: GeneratePlanButtonProps) {
  const queryClient = useQueryClient();
  const router = useRouter();

  const ready = isFinishPlanningReady(messages);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<{ id: string }>(`/projects/${projectId}/plans`, {
        method: "POST",
        body: { conversation_id: conversationId },
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
      router.push(`/admin/projects/${projectId}/plans/${created.id}`);
    },
  });

  if (!ready) {
    return null;
  }

  return (
    <div className="mt-4" data-testid="generate-plan-cta">
      <Button
        data-testid="generate-plan-button"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        Generar Plan
      </Button>
      {mutation.isError ? (
        <p className="text-destructive mt-2 text-xs" data-testid="generate-plan-error">
          {mutation.error instanceof ApiError ? mutation.error.body : String(mutation.error)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Returns true when the most recent `agent` message in the feed has
 * an attachment that signals the planning sub-graph wants to finish.
 *
 * The structure is intentionally permissive — Fase G may add more
 * keys (rationale, estimated_phase_count, ...) but only `kind` and
 * `intent` drive visibility.
 */
export function isFinishPlanningReady(messages: Message[]): boolean {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.author_kind !== "agent") continue;
    for (const att of msg.attachments) {
      if (
        att &&
        typeof att === "object" &&
        (att as Record<string, unknown>).kind === "planning_directive" &&
        (att as Record<string, unknown>).intent === "finish_planning"
      ) {
        return true;
      }
    }
    return false; // most recent agent message had no FINISH directive
  }
  return false;
}

// --------------------------------------------------------------------------
// Message feed
// --------------------------------------------------------------------------
interface MessageFeedProps {
  messages: Message[];
  loading: boolean;
}

function MessageFeed({ messages, loading }: MessageFeedProps) {
  if (loading) {
    return <p className="text-muted-foreground text-sm">Cargando mensajes…</p>;
  }
  if (messages.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="chat-feed-empty">
        La conversación está vacía. Empieza a escribir para comenzar.
      </p>
    );
  }
  return (
    <ol className="space-y-3" data-testid="chat-feed">
      {messages.map((m) => (
        <li key={m.id}>
          <MessageRow message={m} />
        </li>
      ))}
    </ol>
  );
}

function MessageRow({ message }: { message: Message }) {
  if (message.author_kind === "system") {
    return (
      <div
        className={cn(
          "border-muted-foreground/40 rounded border border-dashed",
          "bg-muted/40 text-muted-foreground px-3 py-2 text-center text-xs italic",
        )}
        data-testid="chat-system-banner"
        data-message-id={message.id}
      >
        {message.content}
      </div>
    );
  }
  const tone =
    message.author_kind === "agent"
      ? "border-indigo-500/40 bg-indigo-500/5"
      : "border-emerald-500/40 bg-emerald-500/5";
  // Agents may emit structured plan drafts as markdown (tables, lists,
  // headings). Users type plain text so we only run the renderer for
  // agent turns; user messages stay verbatim.
  const body =
    message.author_kind === "agent" ? (
      renderPlanDraft(message.content)
    ) : (
      <p className="whitespace-pre-wrap">{message.content}</p>
    );
  return (
    <div
      className={cn("rounded border px-3 py-2 text-sm", tone)}
      data-testid={`chat-message-${message.author_kind}`}
    >
      {body}
      <p className="text-muted-foreground mt-1 text-[10px] uppercase tracking-wide">
        {message.author_kind} · {message.mode}
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------
// Composer with @-mention autocomplete (task_03_12)
// --------------------------------------------------------------------------
interface ChatComposerProps {
  disabled: boolean;
  onSubmit: (content: string) => void;
}

function ChatComposer({ disabled, onSubmit }: ChatComposerProps) {
  const [value, setValue] = useState("");
  // Markdown preview toggle. The edit view keeps the raw <textarea> so @-mention
  // tracking (cursor/onChange) stays intact; preview renders the same markdown
  // renderer the chat messages use.
  const [preview, setPreview] = useState(false);
  // The @-trigger is open when the cursor sits in the middle of a
  // partial mention token ("@" followed by 0+ word-chars, no space).
  const mention = parsePendingMention(value);

  const suggestions = mention
    ? PLANNING_ROLES.filter((r) => r.startsWith(mention.query.toLowerCase()))
    : [];

  // Take the text+mention to operate on as explicit args rather than
  // relying on the enclosing closure (frontend-admin-panel-4): the click
  // handler then always splices into exactly the text that produced the
  // visible suggestion list, with no chance of reading a stale `value`.
  const pickMention = (role: string, currentValue: string, target = mention) => {
    if (!target) return;
    const before = currentValue.slice(0, target.start);
    const after = currentValue.slice(target.start + target.length);
    setValue(`${before}@${role} ${after}`);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setValue("");
  };

  return (
    <form className="mt-4 relative" onSubmit={handleSubmit} data-testid="chat-composer">
      <div className="bg-muted mb-1.5 inline-flex w-fit rounded-md p-0.5" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={!preview}
          onClick={() => setPreview(false)}
          data-testid="chat-input-tab-edit"
          className={cn(
            "rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
            !preview ? "bg-background text-foreground shadow" : "text-muted-foreground",
          )}
        >
          Editar
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={preview}
          onClick={() => setPreview(true)}
          data-testid="chat-input-tab-preview"
          className={cn(
            "rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
            preview ? "bg-background text-foreground shadow" : "text-muted-foreground",
          )}
        >
          Vista previa
        </button>
      </div>
      {preview ? (
        <div
          data-testid="chat-input-preview"
          className="bg-muted/30 min-h-[5.5rem] w-full rounded border px-3 py-2 text-sm"
        >
          {value.trim().length === 0 ? (
            <p className="text-muted-foreground/60 text-xs italic">
              Sin contenido para previsualizar.
            </p>
          ) : (
            renderPlanDraft(value)
          )}
        </div>
      ) : (
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Escribe un mensaje. Usa @ para mencionar a un agente. Soporta markdown."
          rows={3}
          disabled={disabled}
          data-testid="chat-input"
          className={cn(
            "w-full resize-none rounded border px-3 py-2 text-sm",
            "bg-background focus:outline-none focus:ring-2 focus:ring-indigo-500/40",
          )}
        />
      )}
      {suggestions.length > 0 ? (
        <ul
          data-testid="mention-suggestions"
          className={cn(
            "bg-popover border-muted absolute left-0 z-10 -mt-2 rounded border",
            "max-h-48 w-64 overflow-y-auto py-1 shadow-md",
          )}
        >
          {suggestions.map((role) => (
            <li key={role}>
              <button
                type="button"
                data-testid={`mention-suggestion-${role}`}
                onClick={() => pickMention(role, value, mention)}
                className="hover:bg-muted w-full px-3 py-1 text-left text-sm"
              >
                @{role}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-2 flex justify-end">
        <Button
          type="submit"
          disabled={disabled || value.trim().length === 0}
          data-testid="chat-send"
        >
          Enviar
        </Button>
      </div>
    </form>
  );
}

/**
 * Returns metadata about the @-mention the user is currently typing
 * (the token immediately before the cursor / value end), or null if
 * there isn't one.
 */
export function parsePendingMention(
  value: string,
): { start: number; length: number; query: string } | null {
  // Match `@word` at the end of the buffer (we don't track caret
  // position here — a simple end-of-text match is good enough for the
  // common "type @ and pick" flow).
  const match = /@(\w*)$/.exec(value);
  if (!match) return null;
  return {
    start: match.index,
    length: match[0].length,
    query: match[1],
  };
}
