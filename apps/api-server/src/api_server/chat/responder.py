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

  * **planning**  — the multi-agent planning sub-graph, STREAMED: the PM's framing, each
                    specialist's contribution and the final synthesis are each published
                    as their own ``agent`` message as soon as they are ready, so the user
                    watches the team work turn-by-turn instead of staring at silence.
  * **discussion**— a single open "ideas & opinions" team reply.
  * **execution** — a single execution-focused team reply (status / next steps).

Provider-agnostic (ADR 0021). Best-effort but never silent: a missing provider, a
timeout, an empty reply or any failure surfaces as a ``system`` message so the user is
never left staring at silence, and the POST is never broken. Each streamed step has its
own timeout + error handling, so one slow/failed specialist never sinks the whole turn.
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
from api_server.chat.planning_graph import (
    PlanningRole,
    PlanningState,
    PMDirective,
    PMIntent,
    SpecialistContribution,
)
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

# Per-step wall-clock backstop. Each streamed planning step (and the single
# discussion/execution reply) is bounded independently so one slow/hung step never
# sinks the whole turn; the orphaned worker thread is bounded by the provider's own
# network timeout. A faster chat model (per-project chat model_config) keeps steps short.
_STEP_TIMEOUT_S = 150.0
_DEFAULT_TEMPERATURE = 0.7

# Spokesperson labels shown as the message heading so the user sees WHO is speaking.
_ROLE_LABELS: dict[PlanningRole, str] = {
    PlanningRole.PROJECT_MANAGER: "🧭 Project Manager",
    PlanningRole.ARCHITECT: "🏗️ Arquitecto",
    PlanningRole.BACKEND_DEV: "⚙️ Backend",
    PlanningRole.FRONTEND_DEV: "🎨 Frontend",
    PlanningRole.QA: "🧪 QA",
    PlanningRole.REVIEWER: "🔍 Reviewer",
    PlanningRole.DEVOPS: "🚀 DevOps",
    PlanningRole.SECURITY: "🔐 Seguridad",
    PlanningRole.TECHNICAL_WRITER: "📝 Documentación",
}

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


def _role_label(role: PlanningRole) -> str:
    return _ROLE_LABELS.get(role, role.value)


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


async def _team_chat_model_config(session: AsyncSession, project: Project | None) -> dict[str, Any]:
    """The team's CHAT-specific model override (``Team.chat_model_config``), empty
    when unset. Lets a project run a lighter/faster model in the interactive chat
    than the (possibly opus+max) model its agents use for real task execution."""
    if project is None or getattr(project, "team_id", None) is None:
        return {}
    team = (
        await session.execute(
            select(Team).where(Team.id == project.team_id, Team.tenant_id == project.tenant_id)
        )
    ).scalar_one_or_none()
    return dict(getattr(team, "chat_model_config", None) or {}) if team is not None else {}


async def resolve_chat_model_config(
    session: AsyncSession, project: Project | None
) -> dict[str, Any]:
    """Resolve the project chat's effective ``model_config``.

    A CHAT-specific override (project then team ``chat_model_config``) wins when set,
    so an interactive chat can use a lighter/faster model than the team's execution
    model. Otherwise it falls back to the normal ADR 0065 inheritance chain
    **platform → project → team** (no per-agent level — the chat speaks for the whole
    team). Never empty: ultimately falls back to the platform default and the code
    default. A corrupt stored config that fails catalogue validation also falls back."""
    project_chat = dict(getattr(project, "chat_model_config", None) or {}) if project else {}
    team_chat = await _team_chat_model_config(session, project)
    platform_default = await get_default_model_config(session)
    project_cfg = dict(getattr(project, "model_config", None) or {}) if project else {}
    team_cfg = await _team_model_config(session, project)
    # Chat override (project → team) is most specific; then the execution chain.
    effective = resolve_model_config_chain(project_chat or None, team_chat or None, None, {})
    if _model_pinned(effective):
        chosen = effective
    else:
        chosen = resolve_model_config_chain(None, team_cfg, project_cfg, platform_default)
    try:
        validate_model_config(chosen)
    except InvalidModelConfigError:
        _log.warning("chat.invalid_model_config_fallback")
        return platform_default
    return chosen


def _model_pinned(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("provider") and cfg.get("model"))


async def team_planning_roles(
    session: AsyncSession, project: Project | None
) -> frozenset[PlanningRole]:
    """The planning spokesperson roles available for this project's team. Always
    includes the PM; specialists come from the team's member agents' roles.
    Joins ``Team`` and filters by tenant as defence-in-depth."""
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


async def team_role_agents(
    session: AsyncSession, project: Project | None
) -> dict[PlanningRole, UUID]:
    """Map each planning role → the team's first agent of that role. Needed for
    author attribution: an ``agent`` message MUST carry ``author_agent_id`` (DB
    CHECK ``ck_messages_author_kind_consistency``). Empty when the project has no
    team."""
    if project is None or getattr(project, "team_id", None) is None:
        return {}
    rows = (
        await session.execute(
            select(Agent.id, Agent.role)
            .join(TeamMember, TeamMember.agent_id == Agent.id)
            .join(Team, Team.id == TeamMember.team_id)
            .where(
                TeamMember.team_id == project.team_id,
                Team.tenant_id == project.tenant_id,
                Agent.tenant_id == project.tenant_id,
                Agent.deleted_at.is_(None),
            )
        )
    ).all()
    out: dict[PlanningRole, UUID] = {}
    for agent_id, role_str in rows:
        try:
            role = PlanningRole(str(role_str))
        except ValueError:
            continue
        out.setdefault(role, agent_id)  # first agent of each role wins
    return out


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
    author_agent_id: UUID | None = None,
) -> None:
    # messages CHECK ck_messages_author_kind_consistency: 'agent' REQUIRES
    # author_agent_id (and author_user_id NULL); 'system' requires both NULL.
    sm = get_admin_sessionmaker()
    async with sm() as session, session.begin():
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            author_kind=author_kind,
            author_agent_id=author_agent_id if author_kind == "agent" else None,
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
            "author_agent_id": (
                str(published.author_agent_id) if published.author_agent_id else None
            ),
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
    error notice can never itself crash the responder."""
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


def _pm_framing(directive: PMDirective, specialists: tuple[PlanningRole, ...]) -> str | None:
    """The PM's opening message when it convenes specialists, so the user sees the
    plan of attack before the contributions arrive. ``None`` when there's nothing
    worth showing (PM answers alone → the synthesis IS the answer)."""
    if not specialists:
        return None
    who = ", ".join(_role_label(r) for r in specialists)
    rationale = directive.rationale.strip()
    head = f"**{_role_label(PlanningRole.PROJECT_MANAGER)}**\n\n"
    body = f"{rationale}\n\n" if rationale else ""
    return f"{head}{body}_Consulto con: {who}_"


async def _stream_planning(
    *,
    model: LLMPlanningModel,
    state: PlanningState,
    tenant_id: UUID,
    conversation_id: UUID,
    mode: str,
    redis: Redis,
    default_agent_id: UUID,
    role_agents: dict[PlanningRole, UUID],
) -> bool:
    """Run one planning turn STEP-BY-STEP, publishing each step as its own ``agent``
    message in real time (PM framing → each specialist → synthesis). Mirrors the
    planning graph's routing. Each step is independently timed + error-guarded.
    Each message is attributed to the speaking role's agent (``default_agent_id`` =
    the PM, used for the framing/synthesis and as fallback). Returns True if at
    least one substantive message was published."""

    async def _step(fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=_STEP_TIMEOUT_S)

    async def _emit(content: str, agent_id: UUID) -> None:
        await _persist_and_publish(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            content=content,
            mode=mode,
            author_kind="agent",
            author_agent_id=agent_id,
            redis=redis,
        )

    published = False

    # 1. PM decides who, if anyone, to bring in.
    try:
        directive: PMDirective = await _step(model.pm_decide, state)
        state.directive = directive
    except Exception as exc:  # first step fatal for the turn (incl. timeout)
        _log.warning(
            "chat.planning_pm_decide_failed",
            conversation_id=str(conversation_id),
            error_type=exc.__class__.__name__,
        )
        return False

    specialists: tuple[PlanningRole, ...] = ()
    if directive.intent == PMIntent.INVITE_SPECIALISTS:
        specialists = tuple(s for s in directive.specialists if s in state.team_roles)

    framing = _pm_framing(directive, specialists)
    if framing:
        await _emit(framing, default_agent_id)
        published = True

    # 2. Specialists, streamed as each finishes (attributed to their own agent).
    contributions: list[SpecialistContribution] = []
    for role in specialists:
        speaker = role_agents.get(role, default_agent_id)
        try:
            contrib: SpecialistContribution = await _step(model.specialist_speak, role, state)
        except Exception as exc:  # skip this specialist only (incl. timeout)
            _log.warning(
                "chat.planning_specialist_failed",
                role=role.value,
                error_type=exc.__class__.__name__,
            )
            await _emit(f"**{_role_label(role)}**\n\n_(no pudo aportar en este turno)_", speaker)
            continue
        contributions.append(contrib)
        state.contributions.append(contrib)
        if contrib.content.strip():
            await _emit(f"**{_role_label(role)}**\n\n{contrib.content}", speaker)
            published = True

    # 3. PM synthesises the turn into the message that moves planning forward.
    try:
        synthesis: str = await _step(model.pm_synthesise, state, contributions)
    except Exception as exc:  # incl. timeout
        _log.warning(
            "chat.planning_synthesise_failed",
            conversation_id=str(conversation_id),
            error_type=exc.__class__.__name__,
        )
        return published  # partial (framing + specialists) is still useful
    if synthesis.strip():
        await _emit(synthesis, default_agent_id)
        published = True
    return published


async def _produce_reply(
    *,
    mode: str,
    provider: LLMProvider,
    api_model: str,
    temperature: float,
    extra: dict[str, Any],
    history: list[dict[str, Any]],
    project_context: dict[str, Any],
    roles: frozenset[PlanningRole],
    role_agents: dict[PlanningRole, UUID],
    default_agent_id: UUID,
    tenant_id: UUID,
    conversation_id: UUID,
    redis: Redis,
) -> None:
    """Run the reply for the resolved provider and publish it. planning → streamed
    sub-graph; discussion/execution → a single reply. Always closes the provider."""
    try:
        if mode == "planning":
            model = LLMPlanningModel(
                provider=provider,
                model=api_model,
                temperature=temperature,
                extra_call_kwargs=extra,
            )
            state = PlanningState(
                chat_history=history,
                project_context=project_context,
                team_roles=roles,
            )
            published = await _stream_planning(
                model=model,
                state=state,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                mode=mode,
                redis=redis,
                default_agent_id=default_agent_id,
                role_agents=role_agents,
            )
            if not published:
                await _system_notice(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    mode=mode,
                    content="El equipo no pudo elaborar una respuesta. Inténtalo de nuevo.",
                    redis=redis,
                )
        else:
            content = await asyncio.wait_for(
                _simple_reply(provider, api_model, mode, history, temperature, extra),
                timeout=_STEP_TIMEOUT_S,
            )
            if content.strip():
                await _persist_and_publish(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    content=content,
                    mode=mode,
                    author_kind="agent",
                    author_agent_id=default_agent_id,
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
    finally:
        # Closing must never mask the real error.
        with contextlib.suppress(Exception):
            await provider.aclose()


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
            # Defence-in-depth on a BYPASSRLS session: never act on another tenant's
            # conversation/project even if invoked with mismatched args.
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
            role_agents = await team_role_agents(session, project)
            # Author for the PM/framing/synthesis + fallback for any role without its
            # own agent. 'agent' messages REQUIRE author_agent_id (DB CHECK).
            default_agent_id = role_agents.get(PlanningRole.PROJECT_MANAGER) or next(
                iter(role_agents.values()), None
            )
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
        # An 'agent' message needs an agent to attribute to. No team agents → the
        # team can't speak; tell the user instead of failing on the DB CHECK.
        if default_agent_id is None:
            with contextlib.suppress(Exception):
                await provider.aclose()
            await _system_notice(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                mode=mode,
                content=(
                    "⚠️ El equipo del proyecto no tiene agentes configurados, así que no "
                    "puede responder en el chat. Asigna un equipo con agentes al proyecto."
                ),
                redis=redis,
            )
            return
        history = history_from_messages(list(rows))
        extra = reasoning_call_kwargs(kind, effective.get("reasoning_effort"))
        await _produce_reply(
            mode=mode,
            provider=provider,
            api_model=api_model,
            temperature=temperature,
            extra=extra,
            history=history,
            project_context=project_context,
            roles=roles,
            role_agents=role_agents,
            default_agent_id=default_agent_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
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
        # bodies / credentials.
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
