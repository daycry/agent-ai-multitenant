"use client";

/**
 * Asistente personal — chat (Plan 10 task assistant-ui).
 *
 * El Tenant Admin pregunta al asistente y recibe una respuesta grounded en
 * las herramientas de solo lectura cross-proyecto (el backend las ejecuta y
 * devuelve `tools_called` + `rounds`). Sólo POST /assistant/chat — no se
 * inventa ningún endpoint.
 *
 * Gating: el asistente es Tenant-Admin-only y está gated por el toggle
 * `personal_assistant_enabled`. El BACKEND devuelve 403; la UI lo refleja
 * mostrando un estado "sin acceso" y NO renderizando el input (la e2e
 * comprueba `assistant-input` con count 0 para el caso member/toggle off).
 *
 * Si quien mira es un Tenant Admin pero el toggle está apagado, el estado
 * "deshabilitado" le indica que puede habilitarlo en Ajustes (con enlace a
 * /admin/assistant/settings). Para un member el mensaje sigue siendo el de
 * "exclusivo para administradores".
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Bot, Phone, Send, Settings } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { VoiceCall } from "@/components/assistant/voice-call";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { useErrorText } from "@/lib/use-error-text";
import {
  ASSISTANT_LIMITS,
  assistantToolLabel,
  getAssistantEnabled,
  type AssistantChatResponse,
  type AssistantToggleState,
  type AssistantConversationItem,
  type AssistantTurnItem,
  streamAssistantChat,
} from "@/lib/assistant";
import { cn } from "@/lib/utils";
import { useCurrentUser } from "@/lib/use-current-user";

interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolsCalled?: string[];
  rounds?: number;
}

let turnSeq = 0;
const nextId = () => `turn-${(turnSeq += 1)}`;

export default function AssistantChatPage() {
  const t = useT("assistant");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const { isTenantAdmin, isLoading: userLoading } = useCurrentUser();
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [forbidden, setForbidden] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  // A1 (hilos persistentes): el hilo activo; null = hilo nuevo al enviar.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Read the on/off toggle (tenant_admin-only, NOT toggle-gated) so a Tenant
  // Admin landing here with the assistant disabled gets a helpful "enable it
  // in Ajustes" message instead of a generic dead end. A member's toggle GET
  // 403s; we treat that the same as "no access".
  const toggleQuery = useQuery<AssistantToggleState, ApiError>({
    queryKey: ["assistant-enabled"],
    queryFn: getAssistantEnabled,
    enabled: isTenantAdmin,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const assistantDisabled = toggleQuery.data ? !toggleQuery.data.enabled : false;

  // A1: hilos del usuario + turnos del hilo activo (persisten entre recargas —
  // human_10_04: el asistente mantiene contexto entre mensajes).
  const conversationsQuery = useQuery<AssistantConversationItem[], ApiError>({
    queryKey: ["assistant-conversations"],
    queryFn: () => apiFetch<AssistantConversationItem[]>("/assistant/conversations"),
    enabled: isTenantAdmin && !assistantDisabled,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const turnsQuery = useQuery<AssistantTurnItem[], ApiError>({
    queryKey: ["assistant-turns", conversationId],
    queryFn: () =>
      apiFetch<AssistantTurnItem[]>(`/assistant/conversations/${conversationId}/turns`),
    enabled: conversationId !== null,
    refetchOnWindowFocus: false,
    retry: false,
  });
  useEffect(() => {
    if (conversationId === null) return;
    const loaded = turnsQuery.data;
    if (!loaded) return;
    setTurns(
      loaded.map((t) => ({
        id: nextId(),
        role: t.role === "assistant" ? "assistant" : "user",
        content: t.content,
        toolsCalled: t.tools_called,
        rounds: t.rounds,
      })),
    );
  }, [conversationId, turnsQuery.data]);

  // A2 fase 1: el turno va por SSE — el «Pensando…» muestra progreso real
  // (ronda + tools) en vez de silencio hasta la respuesta completa.
  // A2 fase 2 (ADR 0073 F2): la redaccion final llega token-a-token en
  // `draftAnswer` y se pinta mientras crece; el frame `answer` final la fija.
  const [progressNote, setProgressNote] = useState<string | null>(null);
  const [draftAnswer, setDraftAnswer] = useState<string>("");
  const mutation = useMutation<AssistantChatResponse, ApiError, string>({
    mutationFn: (message) => {
      setDraftAnswer("");
      return streamAssistantChat(
        message,
        conversationId,
        (frame) => {
          const tools = frame.tools_called.length
            ? ` — ${frame.tools_called[frame.tools_called.length - 1]}`
            : "";
          setProgressNote(frame.rounds > 0 ? t("progressRound", { n: frame.rounds, tools }) : null);
        },
        (delta) => setDraftAnswer((prev) => prev + delta),
      );
    },
    onSuccess: (data) => {
      setProgressNote(null);
      setDraftAnswer("");
      if (data.conversation_id) setConversationId(data.conversation_id);
      setTurns((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "assistant",
          content: data.answer,
          toolsCalled: data.tools_called,
          rounds: data.rounds,
        },
      ]);
    },
    onError: (error) => {
      setProgressNote(null);
      setDraftAnswer("");
      // A 403 here means the toggle was flipped off (or the role changed)
      // after load: reflect the backend's gate rather than showing a chat
      // surface we know is denied.
      if (error.status === 403) setForbidden(true);
    },
  });

  // While we don't yet know the role (or the toggle state for an admin),
  // render nothing interactive — never flash the chat input before we know
  // the user may use it.
  if (userLoading || (isTenantAdmin && toggleQuery.isLoading)) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
        <p className="text-muted-foreground flex items-center gap-2 text-sm">
          <Spinner />
          {tCommon("loading")}
        </p>
      </div>
    );
  }

  // Member / toggle off / 403 -> no chat input at all (e2e: count 0). An
  // admin whose tenant has the assistant disabled gets the "enable it in
  // Ajustes" variant; a member gets the plain "admins only" message.
  if (!isTenantAdmin || forbidden || assistantDisabled) {
    return <AssistantNoAccess canEnable={isTenantAdmin && (assistantDisabled || forbidden)} />;
  }

  const trimmed = draft.trim();
  const canSend = trimmed.length > 0 && !mutation.isPending;

  const submit = () => {
    if (!canSend) return;
    const message = trimmed.slice(0, ASSISTANT_LIMITS.message.max);
    setTurns((prev) => [...prev, { id: nextId(), role: "user", content: message }]);
    setDraft("");
    mutation.mutate(message);
    inputRef.current?.focus();
  };

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <Button
              variant={voiceMode ? "default" : "outline"}
              onClick={() => setVoiceMode((v) => !v)}
              data-testid="assistant-voice-toggle"
            >
              <Phone className="mr-2 h-4 w-4" />
              {voiceMode ? t("voiceClose") : t("voiceMode")}
            </Button>
            <Button variant="outline" asChild>
              <Link href="/admin/assistant/settings">
                <Settings className="mr-2 h-4 w-4" />
                {t("identityLink")}
              </Link>
            </Button>
          </>
        }
        data-testid="assistant-chat-header"
      />

      {/* La videollamada es un overlay a pantalla completa (shell compartida). */}
      {voiceMode ? <VoiceCall onClose={() => setVoiceMode(false)} /> : null}

      {/* A1: hilos persistentes — cambia de conversación o empieza una nueva
          sin perder las demás (los turnos viven en el backend). */}
      <div className="mt-4 flex flex-wrap items-center gap-2" data-testid="assistant-history-bar">
        <label htmlFor="assistant-conversation-picker" className="text-muted-foreground text-sm">
          {t("threadLabel")}
        </label>
        <select
          id="assistant-conversation-picker"
          data-testid="assistant-conversation-picker"
          className="bg-background w-full min-w-0 rounded-md border px-2 py-1 text-sm sm:w-72"
          value={conversationId ?? ""}
          onChange={(e) => {
            const value = e.target.value;
            if (!value) {
              setConversationId(null);
              setTurns([]);
            } else {
              setConversationId(value);
            }
          }}
        >
          <option value="">{t("threadNew")}</option>
          {(conversationsQuery.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.title ?? c.id.slice(0, 8)}
            </option>
          ))}
        </select>
      </div>

      <Card className="mt-6">
        <CardContent className="flex flex-col gap-4 pt-5">
          <div
            data-testid="assistant-chat"
            className="flex min-h-[16rem] flex-col gap-3"
            aria-live="polite"
          >
            {turns.length === 0 && !mutation.isPending ? (
              <EmptyState icon={Bot} title={t("emptyTitle")} description={t("emptyDescription")} />
            ) : (
              turns.map((turn) => <ChatBubble key={turn.id} turn={turn} />)
            )}
            {mutation.isPending && draftAnswer ? (
              // A2 fase 2: la respuesta se pinta mientras llega (token-a-token).
              <ChatBubble turn={{ id: "draft", role: "assistant", content: draftAnswer }} />
            ) : null}
            {mutation.isPending && !draftAnswer ? (
              <p
                className="text-muted-foreground flex items-center gap-2 text-sm"
                data-testid="assistant-thinking"
              >
                <Spinner />
                {progressNote ? t("thinkingWith", { note: progressNote }) : t("thinking")}
              </p>
            ) : null}
          </div>

          {mutation.isError && !forbidden ? (
            <p className="text-destructive text-sm" data-testid="assistant-chat-error">
              {errorText(mutation.error)}
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
              ref={inputRef}
              data-testid="assistant-input"
              aria-label={t("inputAria")}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={ASSISTANT_LIMITS.message.max}
              placeholder={t("inputPlaceholder")}
              className="border-input bg-background placeholder:text-muted-foreground focus-visible:ring-ring focus-visible:ring-offset-background flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
            />
            <Button type="submit" data-testid="assistant-send" disabled={!canSend}>
              <Send className="mr-2 h-4 w-4" />
              {t("send")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function ChatBubble({ turn }: { turn: ChatTurn }) {
  const t = useT("assistant");
  const lang = useLangOptional();
  const isUser = turn.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        data-testid={isUser ? "assistant-question" : "assistant-answer"}
        className={cn(
          "max-w-[85%] rounded-lg px-3 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted text-foreground border",
        )}
      >
        {isUser ? (
          // User text stays verbatim (never markdown-rendered).
          <p className="whitespace-pre-wrap">{turn.content}</p>
        ) : (
          // Assistant answers (status/plan tables, lists) render as markdown via the
          // shared XSS-safe renderer — consistent with the project chat + markdown-everywhere.
          renderPlanDraft(turn.content)
        )}
        {!isUser && turn.toolsCalled && turn.toolsCalled.length > 0 ? (
          <div className="mt-2 flex flex-wrap gap-1" data-testid="assistant-answer-tools">
            {turn.toolsCalled.map((tool) => (
              <span
                key={tool}
                className="bg-background text-muted-foreground rounded border px-1.5 py-0.5 text-[10px]"
              >
                {assistantToolLabel(tool, lang)}
              </span>
            ))}
          </div>
        ) : null}
        {!isUser && typeof turn.rounds === "number" ? (
          <p className="text-muted-foreground mt-1 text-[10px] uppercase tracking-wide">
            {turn.rounds === 1 ? t("roundsOne") : t("roundsMany", { n: turn.rounds })}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function AssistantNoAccess({ canEnable = false }: { canEnable?: boolean }) {
  const t = useT("assistant");
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        data-testid="assistant-chat-header"
      />
      <EmptyState
        data-testid="assistant-no-access"
        icon={Bot}
        title={t("noAccessTitle")}
        description={canEnable ? t("noAccessDisabled") : t("noAccessMember")}
        action={
          canEnable ? (
            <Button asChild data-testid="assistant-enable-cta">
              <Link href="/admin/assistant/settings">
                <Settings className="mr-2 h-4 w-4" />
                {t("goToSettings")}
              </Link>
            </Button>
          ) : undefined
        }
      />
    </div>
  );
}
