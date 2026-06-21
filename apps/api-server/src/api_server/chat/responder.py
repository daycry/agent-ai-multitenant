"""Generate the team's reply to a project-chat message (the missing Plan 04 wiring).

``post_message`` persists the user message and — AFTER that row commits
(``schedule_after_commit``, never ``BackgroundTasks``: in FastAPI the yield-dependency
commit runs *after* background tasks, so a background task would read the not-yet-
committed message) — spawns ``respond_to_conversation`` as a detached task so the
POST returns immediately without blocking on the LLM.

The reply is produced by the PROJECT'S TEAM, so the model is resolved by the ADR 0065 /
0055 inheritance chain **platform → project → team** (the per-agent level is excluded on
purpose: the chat speaks for the whole team, not one agent), and the concrete provider
is picked **by kind, newest active row** (the same dispatch path agent execution uses,
ADR 0028). This is deliberately NOT the personal-assistant model setting.

Per chat mode:

  * **planning**  — the multi-agent planning sub-graph (PM + the project team's real
                    specialist roles → synthesis).
  * **discussion**— a single open "ideas & opinions" team reply.
  * **execution** — a single execution-focused team reply (status / next steps).

Provider-agnostic (ADR 0021). Best-effort but never silent: a missing provider, a
timeout, an empty reply or any failure surfaces as a ``system`` message so the user is
never left staring at silence, and the POST is never broken.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from shared_llm.base import LLMProvider
from shared_llm.reasoning import reasoning_call_kwargs
from shared_llm.types import Message as LLMMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.model_config import to_provider_model_name
from api_server.chat.planning_graph import PlanningRole, run_planning_turn
from api_server.chat.planning_llm import LLMPlanningModel
from api_server.db.conversation import Conversation, Message
from api_server.db.domain import Agent, Project, Team, TeamMember
from api_server.db.platform_settings import (
    InvalidModelConfigError,
    get_default_model_config,
    resolve_model_config_chain,
    validate_model_config,
)
from api_server.db.session import get_admin_sessionmaker
from api_server.events import EVENT_MESSAGE_CREATED, publish_conversation_event
from api_server.llm_providers.factory import build_provider_from_kind
from api_server.llm_providers.factory_resolver import resolve_provider_config
from api_server.llm_providers.vault import LLMProviderVaultStore

_log = structlog.get_logger("api_server.chat.responder")

# Wall-clock backstop for a single chat turn (planning may chain several LLM calls).
# On timeout we surface a ``system`` notice; the orphaned worker thread is bounded by
# the provider's own network timeout. Keeps a hung provider from leaking threads.
_RESPONDER_TIMEOUT_S = 180.0
_DEFAULT_TEMPERATURE = 0.7

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

# Detached reply tasks — held so the event loop does not GC them mid-flight.
_PENDING_REPLIES: set[asyncio.Task[None]] = set()


def history_from_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Map persisted conversation messages to the {role, content} dicts the LLM
    seam expects (agent → assistant; user/system pass through)."""
    return [{"role": _ROLE_MAP.get(m.author_kind, "user"), "content": m.content} for m in messages]


def planning_roles_from_strings(role_strings: Iterable[str]) -> frozenset[PlanningRole]:
    """Map raw ``Agent.role`` strings to the planning spokesperson roles.

    Unknown roles (e.g. ``researcher``, which the planning graph does not invite
    by name) are dropped. The PROJECT_MANAGER is always present — it is the only
    required role and drives every planning turn."""
    roles: set[PlanningRole] = {PlanningRole.PROJECT_MANAGER}
    for raw in role_strings:
        try:
            roles.add(PlanningRole(str(raw)))
        except ValueError:
            continue
    return frozenset(roles)


async def _team_model_config(session: AsyncSession, project: Project | None) -> dict[str, Any]:
    """``model_config`` of the project's team (empty when no team / not found).
    Filters by the project's tenant as defence-in-depth (admin session bypasses
    RLS)."""
    if project is None:
        return {}
    team_id = getattr(project, "team_id", None)
    if team_id is None:
        return {}
    team = (
        await session.execute(
            select(Team).where(Team.id == team_id, Team.tenant_id == project.tenant_id)
        )
    ).scalar_one_or_none()
    return dict(team.model_config or {}) if team is not None else {}


async def resolve_chat_model_config(
    session: AsyncSession, project: Project | None
) -> dict[str, Any]:
    """Resolve the project chat's effective ``model_config`` via the ADR 0065
    inheritance chain **platform → project → team** (no per-agent level — the
    chat speaks for the whole team). Never empty: falls back to the platform
    default and then the code default, exactly like agent dispatch. A corrupt
    stored config that fails catalogue validation also falls back (M2)."""
    platform_default = await get_default_model_config(session)
    project_cfg = dict(getattr(project, "model_config", None) or {}) if project else {}
    team_cfg = await _team_model_config(session, project)
    # agent_cfg=None → resolve at team/project/platform granularity.
    effective = resolve_model_config_chain(None, team_cfg, project_cfg, platform_default)
    try:
        validate_model_config(effective)
    except InvalidModelConfigError:
        _log.warning("chat.invalid_model_config_fallback")
        return platform_default
    return effective


async def team_planning_roles(
    session: AsyncSession, project: Project | None
) -> frozenset[PlanningRole]:
    """The planning spokesperson roles available for this project's team. Always
    includes the PM; specialists come from the team's member agents' roles.
    Joins ``Team`` and filters by tenant as defence-in-depth (M4)."""
    if project is None or getattr(project, "team_id", None) is None:
        return frozenset({PlanningRole.PROJECT_MANAGER})
    rows = (
        (
            await session.execute(
                select(Agent.role)
                .join(TeamMember, TeamMember.agent_id == Agent.id)
                .join(Team, Team.id == TeamMember.team_id)
                .where(
                    TeamMember.team_id == project.team_id,
                    Team.tenant_id == project.tenant_id,
                    Agent.tenant_id == project.tenant_id,
                    Agent.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return planning_roles_from_strings(rows)


async def build_chat_provider(
    session: AsyncSession, *, kind: str, model: str, vault: LLMProviderVaultStore | None
) -> LLMProvider | None:
    """Build the concrete provider for ``kind`` from its newest ACTIVE
    ``llm_providers`` row + Vault credential (the dispatch resolution path).
    ``None`` when no active row of that kind exists or it cannot be built."""
    resolved = await resolve_provider_config(session, kind, vault=vault)
    if resolved is None:
        return None
    return build_provider_from_kind(
        kind, base_url=resolved.base_url, secret=resolved.secret, model=model
    )


async def _simple_reply(
    provider: LLMProvider,
    model: str,
    mode: str,
    history: list[dict[str, Any]],
    temperature: float,
    extra: dict[str, Any],
) -> str:
    """A single team reply for non-planning modes (discussion / execution)."""
    system = _MODE_PROMPTS.get(mode, _MODE_PROMPTS["discussion"])
    messages = [LLMMessage(role="system", content=system)]
    for entry in history:
        role = entry["role"] if entry["role"] in ("user", "assistant", "system") else "user"
        messages.append(LLMMessage(role=role, content=str(entry["content"])))
    resp = await provider.complete(messages, model=model, temperature=temperature, **extra)
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


async def _system_notice(
    *, tenant_id: UUID, conversation_id: UUID, mode: str, content: str, redis: Redis
) -> None:
    """Publish a ``system`` message, swallowing any persist/publish failure so an
    error notice can never itself crash the responder (A3 / M-level)."""
    try:
        await _persist_and_publish(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            content=content,
            mode=mode,
            author_kind="system",
            redis=redis,
        )
    except Exception as exc:  # best-effort notice
        _log.warning(
            "chat.system_notice_failed",
            conversation_id=str(conversation_id),
            error_type=exc.__class__.__name__,
        )


async def _generate_reply(
    *,
    mode: str,
    provider: LLMProvider,
    api_model: str,
    history: list[dict[str, Any]],
    temperature: float,
    extra: dict[str, Any],
    roles: frozenset[PlanningRole],
    project_context: dict[str, Any],
) -> str:
    if mode == "planning":
        model = LLMPlanningModel(
            provider=provider,
            model=api_model,
            temperature=temperature,
            extra_call_kwargs=extra,
        )
        # The planning graph is sync (asyncio.run per LLM call inside the adapter);
        # run it in a worker thread so that nested loop is its own.
        result = await asyncio.to_thread(
            run_planning_turn,
            model,
            chat_history=history,
            project_context=project_context,
            team_roles=roles,
        )
        return result.content
    return await _simple_reply(provider, api_model, mode, history, temperature, extra)


async def respond_to_conversation(
    *,
    conversation_id: UUID,
    tenant_id: UUID,
    mode: str,
    vault: LLMProviderVaultStore | None,
    redis: Redis,
) -> None:
    """Produce + persist the project team's reply to the latest message. Best-effort."""
    sm = get_admin_sessionmaker()
    try:
        async with sm() as session:
            conv = (
                await session.execute(
                    select(Conversation).where(Conversation.id == conversation_id)
                )
            ).scalar_one_or_none()
            if conv is None:
                return
            # Defence-in-depth on a BYPASSRLS session (C2): never act on another
            # tenant's conversation/project even if invoked with mismatched args.
            if conv.tenant_id != tenant_id:
                _log.warning("chat.responder_cross_tenant_conversation", id=str(conversation_id))
                return
            project = (
                await session.execute(select(Project).where(Project.id == conv.project_id))
            ).scalar_one_or_none()
            if project is not None and project.tenant_id != tenant_id:
                _log.warning("chat.responder_cross_tenant_project", id=str(project.id))
                return

            effective = await resolve_chat_model_config(session, project)
            kind = str(effective.get("provider") or "")
            api_model = to_provider_model_name(kind, str(effective.get("model") or ""))
            temperature = float(effective.get("temperature", _DEFAULT_TEMPERATURE))
            provider = await build_chat_provider(session, kind=kind, model=api_model, vault=vault)
            if provider is None:
                await _system_notice(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    mode=mode,
                    content=(
                        f"⚠️ No hay un proveedor LLM activo del tipo «{kind or '—'}» para que "
                        "el equipo responda. Pide a un administrador que active un proveedor "
                        "de ese tipo, o fija otro modelo en el proyecto o el equipo."
                    ),
                    redis=redis,
                )
                return
            roles = await team_planning_roles(session, project)
            project_context = (
                {"name": project.name, "description": project.description or ""}
                if project is not None
                else {}
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
        history = history_from_messages(list(rows))
        extra = reasoning_call_kwargs(kind, effective.get("reasoning_effort"))
        try:
            content = await asyncio.wait_for(
                _generate_reply(
                    mode=mode,
                    provider=provider,
                    api_model=api_model,
                    history=history,
                    temperature=temperature,
                    extra=extra,
                    roles=roles,
                    project_context=project_context,
                ),
                timeout=_RESPONDER_TIMEOUT_S,
            )
        finally:
            # Closing must never mask the real error (M6).
            with contextlib.suppress(Exception):
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
        else:
            await _system_notice(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                mode=mode,
                content="El equipo procesó tu mensaje pero no tuvo nada que añadir.",
                redis=redis,
            )
    except TimeoutError:
        _log.warning("chat.responder_timeout", conversation_id=str(conversation_id), mode=mode)
        await _system_notice(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            mode=mode,
            content="⌛ El equipo tardó demasiado en responder. Inténtalo de nuevo.",
            redis=redis,
        )
    except Exception as exc:  # never let a chat reply crash the background task
        # Log the exception TYPE, not str(exc): provider errors can embed response
        # bodies / credentials (M5).
        _log.warning(
            "chat.responder_failed",
            conversation_id=str(conversation_id),
            mode=mode,
            error_type=exc.__class__.__name__,
        )
        await _system_notice(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            mode=mode,
            content="⚠️ El equipo no pudo responder por un error temporal. Inténtalo de nuevo.",
            redis=redis,
        )


def schedule_reply(
    *,
    conversation_id: UUID,
    tenant_id: UUID,
    mode: str,
    vault: LLMProviderVaultStore | None,
    redis: Redis,
) -> Callable[[], Awaitable[None]]:
    """Return an after-commit factory (for ``schedule_after_commit``) that spawns
    ``respond_to_conversation`` as a DETACHED task.

    Why both: ``schedule_after_commit`` guarantees the user message is durable
    before the responder reads the history (fixes the pre-commit race), while the
    detached task keeps the POST response from blocking on the LLM call. The task
    is tracked in ``_PENDING_REPLIES`` so the loop does not GC it mid-flight."""

    async def _factory() -> None:
        task = asyncio.create_task(
            respond_to_conversation(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                mode=mode,
                vault=vault,
                redis=redis,
            )
        )
        _PENDING_REPLIES.add(task)
        task.add_done_callback(_PENDING_REPLIES.discard)

    return _factory


__all__ = [
    "build_chat_provider",
    "history_from_messages",
    "planning_roles_from_strings",
    "resolve_chat_model_config",
    "respond_to_conversation",
    "schedule_reply",
    "team_planning_roles",
]
