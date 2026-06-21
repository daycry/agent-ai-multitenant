"""Generate the team's reply to a project-chat message (the missing Plan 04 wiring).

``post_message`` persists the user message and schedules ``respond_to_conversation``
as a background task. It resolves the tenant's assistant LLM (provider-agnostic,
ADR 0021 — the same provider the assistant uses), builds the reply per chat mode and
persists it as an ``agent`` message + publishes the WS event the UI already tails:

  * **planning**  — the multi-agent planning sub-graph (PM + specialists → synthesis).
  * **discussion**— a single open "ideas & opinions" team reply.
  * **execution** — a single execution-focused team reply (status / next steps).

Best-effort: any failure is logged and (when possible) surfaced as a ``system``
message, so a misconfigured provider never leaves the user staring at silence.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from shared_llm.base import LLMProvider
from shared_llm.reasoning import reasoning_call_kwargs
from shared_llm.types import Message as LLMMessage
from sqlalchemy import select

from api_server.assistant.model_config import resolve_assistant_model, to_provider_model_name
from api_server.chat.planning_graph import run_planning_turn
from api_server.chat.planning_llm import LLMPlanningModel
from api_server.db.conversation import Message
from api_server.db.session import get_admin_sessionmaker
from api_server.events import EVENT_MESSAGE_CREATED, publish_conversation_event
from api_server.llm_providers.factory import build_llm_provider
from api_server.llm_providers.vault import LLMProviderVaultStore

_log = structlog.get_logger("api_server.chat.responder")

_MODE_PROMPTS: dict[str, str] = {
    "discussion": (
        "Eres el portavoz del equipo del proyecto en una RONDA DE DISCUSIÓN abierta. "
        "Aporta ideas, opiniones y alternativas de forma clara y concisa (markdown). "
        "NO generes un plan estructurado: es un intercambio abierto."
    ),
    "execution": (
        "Eres el portavoz del equipo del proyecto en modo EJECUCIÓN. Ayuda a coordinar "
        "y seguir el trabajo: responde con foco operativo (estado, siguientes pasos, "
        "bloqueos, decisiones), claro y conciso (markdown)."
    ),
}

_ROLE_MAP = {"user": "user", "agent": "assistant", "system": "system"}


def history_from_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Map persisted conversation messages to the {role, content} dicts the LLM
    seam expects (agent → assistant; user/system pass through)."""
    return [{"role": _ROLE_MAP.get(m.author_kind, "user"), "content": m.content} for m in messages]


async def _simple_reply(
    provider: LLMProvider,
    model: str,
    mode: str,
    history: list[dict[str, Any]],
    extra: dict[str, Any],
) -> str:
    """A single team reply for non-planning modes (discussion / execution)."""
    system = _MODE_PROMPTS.get(mode, _MODE_PROMPTS["discussion"])
    messages = [LLMMessage(role="system", content=system)]
    for entry in history:
        role = entry["role"] if entry["role"] in ("user", "assistant", "system") else "user"
        messages.append(LLMMessage(role=role, content=str(entry["content"])))
    resp = await provider.complete(messages, model=model, **extra)
    return resp.content or ""


async def _persist_and_publish(
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    content: str,
    mode: str,
    author_kind: str,
    redis: Redis,
) -> None:
    sm = get_admin_sessionmaker()
    async with sm() as session, session.begin():
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            author_kind=author_kind,
            content=content,
            mode=mode,
        )
        session.add(message)
        await session.flush()
        await session.refresh(message)
        published = message
    await publish_conversation_event(
        redis,
        str(published.conversation_id),
        event_type=EVENT_MESSAGE_CREATED,
        payload={
            "message_id": str(published.id),
            "author_kind": published.author_kind,
            "author_user_id": None,
            "author_agent_id": None,
            "content": published.content,
            "mode": published.mode,
            "attachments": published.attachments,
            "is_summary": published.is_summary,
        },
    )


async def respond_to_conversation(
    *,
    conversation_id: UUID,
    tenant_id: UUID,
    mode: str,
    vault: LLMProviderVaultStore | None,
    redis: Redis,
) -> None:
    """Produce + persist the team's reply to the latest message. Best-effort."""
    sm = get_admin_sessionmaker()
    try:
        async with sm() as session:
            resolved = await resolve_assistant_model(session, tenant_id)
            if resolved is None:
                await _persist_and_publish(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    content=(
                        "⚠️ No hay un proveedor LLM configurado para el chat. "
                        "Configúralo en Ajustes del asistente para que el equipo responda."
                    ),
                    mode=mode,
                    author_kind="system",
                    redis=redis,
                )
                return
            api_model = to_provider_model_name(resolved.provider_kind, resolved.model_id)
            provider = await build_llm_provider(
                session, provider_id=resolved.provider_id, model=api_model, vault=vault
            )
            rows = (
                (
                    await session.execute(
                        select(Message)
                        .where(
                            Message.conversation_id == conversation_id,
                            Message.tenant_id == tenant_id,
                        )
                        .order_by(Message.created_at)
                        .limit(50)
                    )
                )
                .scalars()
                .all()
            )
        if provider is None:
            return
        history = history_from_messages(list(rows))
        extra = reasoning_call_kwargs(resolved.provider_kind, resolved.reasoning_effort)
        try:
            if mode == "planning":
                model = LLMPlanningModel(
                    provider=provider, model=api_model, extra_call_kwargs=extra
                )
                # The planning graph is sync (asyncio.run per LLM call inside the
                # adapter); run it in a worker thread so that nested loop is its own.
                result = await asyncio.to_thread(
                    run_planning_turn, model, chat_history=history, project_context={}
                )
                content = result.content
            else:
                content = await _simple_reply(provider, api_model, mode, history, extra)
        finally:
            await provider.aclose()
        if content.strip():
            await _persist_and_publish(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                content=content,
                mode=mode,
                author_kind="agent",
                redis=redis,
            )
    except Exception as exc:  # never let a chat reply crash the background task
        _log.warning(
            "chat.responder_failed", conversation_id=str(conversation_id), mode=mode, error=str(exc)
        )


__all__ = ["history_from_messages", "respond_to_conversation"]
