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
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import structlog
from redis.asyncio import Redis
from shared_llm.base import LLMProvider
from shared_llm.reasoning import reasoning_call_kwargs
from shared_llm.types import Message as LLMMessage
from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
from api_server.db.domain import Agent, Plan, Project, Team, TeamMember
from api_server.db.llm_providers import get_llm_provider
from api_server.db.memory import MemoryEntry
from api_server.db.platform_settings import (
    InvalidModelConfigError,
    get_default_model_config,
    resolve_model_config_chain,
    validate_model_config,
)
from api_server.db.session import get_admin_sessionmaker
from api_server.events import EVENT_MESSAGE_CREATED, publish_conversation_event
from api_server.ingestion.embeddings import OllamaEmbedder
from api_server.llm_providers.factory import build_llm_provider, build_provider_from_kind
from api_server.llm_providers.factory_resolver import resolve_provider_config
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.rag.tool import rag_search

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
    # Feature B: a chat override pinned to a CONCRETE provider (provider_id + model)
    # wins as-is (project → team), bypassing the kind-based chain/validation — the
    # provider row + its kind are resolved at build time.
    for override in (project_chat, team_chat):
        if override.get("provider_id") and override.get("model"):
            return dict(override)
    platform_default = await get_default_model_config(session)
    project_cfg = dict(getattr(project, "model_config", None) or {}) if project else {}
    team_cfg = await _team_model_config(session, project)
    # Kind-based chat override (project → team) is most specific; then the exec chain.
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


def _plan_summary(plan: Plan) -> str:
    spec = plan.specification if isinstance(plan.specification, dict) else {}
    summary = spec.get("summary")
    if isinstance(summary, dict):
        return str(summary.get("description") or summary.get("title") or "")[:300]
    return str(summary or plan.description or "")[:300]


async def build_project_context(
    session: AsyncSession,
    project: Project | None,
    query_text: str,
    *,
    agent_id: UUID | None = None,
    embedder: Any | None = None,
) -> dict[str, Any]:
    """Assemble what the team needs to know to plan well in an EXISTING project:
    identity + prior plans + project-scoped memories + relevant docs/code (RAG).

    This is the provider-agnostic answer to "a 2nd plan should know how the project
    is built": instead of an agent browsing the repo (which would only work natively
    for claude_sdk), the system RETRIEVES the context and injects it into the planning
    prompt (``project_context`` already reaches every planning prompt). Identical for
    ollama / claude_sdk / azure / copilot. Each source is best-effort: a failure is
    omitted, never sinks the turn.

    P0-5 (investigación 2026-07-11): ``agent_id`` une las KBs granted al agente
    (rol — ``agent_knowledge_bases``, Plan 06.9) con las del proyecto, y
    ``embedder`` habilita el path vectorial del recall híbrido — sin él el
    planning era BM25-only (``vector_chunks`` devolvía siempre []). Ambos son
    opcionales y best-effort (Ollama caído → BM25, igual que los agentes)."""
    if project is None:
        return {}
    ctx: dict[str, Any] = {"name": project.name, "description": project.description or ""}

    # Prior plans — so a follow-up plan knows what was already planned/built.
    with contextlib.suppress(Exception):
        plans = (
            (
                await session.execute(
                    select(Plan)
                    .where(Plan.project_id == project.id, Plan.deleted_at.is_(None))
                    .order_by(Plan.created_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        if plans:
            ctx["prior_plans"] = [
                {"title": p.title, "status": p.status, "summary": _plan_summary(p)} for p in plans
            ]

    # Project-shared memories — what the team learned about THIS project.
    with contextlib.suppress(Exception):
        mems = (
            (
                await session.execute(
                    select(MemoryEntry.content)
                    .where(
                        MemoryEntry.tenant_id == project.tenant_id,
                        MemoryEntry.scope == "project_shared",
                        MemoryEntry.project_id == project.id,
                    )
                    .order_by(MemoryEntry.created_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        if mems:
            ctx["memories"] = [str(m) for m in mems]

    # Relevant docs/code from the project's KBs — the same end-to-end retrieval
    # the agents use (rag_search: embed best-effort → hybrid recall → top-N),
    # including the agent's role-granted KBs when we know who plans (P0-5).
    if query_text.strip():
        with contextlib.suppress(Exception):
            hits = await rag_search(
                session,
                query=query_text,
                tenant_id=project.tenant_id,
                project_id=project.id,
                agent_id=agent_id,
                limit=5,
                embedder=embedder,
            )
            if hits:
                ctx["docs"] = [h.content[:500] for h in hits]
    return ctx


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


async def _resolve_chat_provider(
    session: AsyncSession, effective: dict[str, Any], vault: LLMProviderVaultStore | None
) -> tuple[LLMProvider | None, str, str]:
    """Build the chat provider + resolve its kind and API model name. Two paths:
    a CONCRETE provider pinned by ``provider_id`` (Feature B → built from THAT row),
    or a kind (built from the newest active row of the kind, the dispatch path).
    Returns ``(provider|None, kind, api_model)``."""
    pid = effective.get("provider_id")
    if pid:
        try:
            provider_uuid = UUID(str(pid))
        except (ValueError, TypeError):
            return None, "", ""
        row = await get_llm_provider(session, provider_uuid)
        if row is None or not row.is_active:
            return None, "", ""
        api_model = to_provider_model_name(row.kind, str(effective.get("model") or ""))
        provider = await build_llm_provider(
            session, provider_id=provider_uuid, model=api_model, vault=vault
        )
        return provider, row.kind, api_model
    kind = str(effective.get("provider") or "")
    api_model = to_provider_model_name(kind, str(effective.get("model") or ""))
    provider = await build_chat_provider(session, kind=kind, model=api_model, vault=vault)
    return provider, kind, api_model


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
        raw_role = str(entry["role"])
        # El history llega de JSONB (str libre); LLMMessage.role es un Literal —
        # el `in` garantiza el valor en runtime, el cast solo informa a mypy.
        role = cast(
            Literal["system", "user", "assistant"],
            raw_role if raw_role in ("user", "assistant", "system") else "user",
        )
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
    attachments: list[dict[str, Any]] | None = None,
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
            attachments=attachments or [],
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


# The structured plan-draft (pm_plan_draft) is a SEPARATE call from the prose
# synthesis and can come back empty on a slow/flaky model (e.g. a local model
# under load, or a step timeout) even when the synthesis is a complete plan.
# Retry it once before giving up so a transient miss doesn't cost the user the
# "Generar Plan" button.
_DRAFT_ATTEMPTS = 2


def _finish_planning_attachment(draft: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Build the finish_planning attachment — the UI's "Generar Plan" button
    contract (``isFinishPlanningReady``) that lets ``create_plan`` materialise
    the tasks — from a structured plan draft. ``None`` when the draft has no
    usable tasks (self-gating: a clarifying/empty turn attaches nothing)."""
    if not draft.get("tasks"):
        return None
    return [
        {
            "kind": "planning_directive",
            "intent": "finish_planning",
            "title": draft.get("title") or "Plan del proyecto",
            "specification": {
                # A-03: OBJETO, no cadena — `PlanSpecification.summary` es un dict
                # y el draft se persiste sin pasar por Pydantic, así que emitir la
                # forma correcta AQUÍ es lo que evita el 422 posterior.
                # `_normalise_plan_draft` ya lo deja normalizado.
                "summary": draft.get("summary") or {},
                "phases": draft.get("phases") or [],
                "tasks": draft["tasks"],
            },
        }
    ]


async def _resolve_plan_attachment(
    model: LLMPlanningModel,
    state: PlanningState,
    contributions: list[SpecialistContribution],
    conversation_id: UUID,
) -> list[dict[str, Any]] | None:
    """Run ``pm_plan_draft`` (bounded by ``_STEP_TIMEOUT_S``) and turn it into the
    finish_planning attachment, RETRYING once. The structured draft is a separate
    call from the prose synthesis and can come back empty/time out on a slow or
    flaky model; one retry recovers the button on a transient miss. Best-effort:
    returns ``None`` (no button) if both attempts fail — never raises."""
    for attempt in range(_DRAFT_ATTEMPTS):
        try:
            draft = await asyncio.wait_for(
                asyncio.to_thread(model.pm_plan_draft, state, contributions),
                timeout=_STEP_TIMEOUT_S,
            )
        except Exception as exc:  # best-effort (incl. timeout); retry then give up
            _log.warning(
                "chat.planning_draft_failed",
                conversation_id=str(conversation_id),
                attempt=attempt,
                error_type=exc.__class__.__name__,
            )
            continue
        attachments = _finish_planning_attachment(draft)
        if attachments is not None:
            return attachments
    return None


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

    async def _emit(
        content: str, agent_id: UUID, attachments: list[dict[str, Any]] | None = None
    ) -> None:
        await _persist_and_publish(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            content=content,
            mode=mode,
            author_kind="agent",
            author_agent_id=agent_id,
            attachments=attachments,
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

    # In plan-only planning mode the deliverable is ALWAYS an insertable plan, so formalise
    # the synthesis as a structured draft and attach it whenever the PM PRESENTS a plan — not
    # only on the rarely self-selected FINISH_PLANNING intent (a complete plan produced under
    # SPEAK_ALONE / INVITE_SPECIALISTS must still offer "Generar Plan"). ASK_USER is the one
    # exception: there the PM is asking the user a question, not presenting a plan. pm_plan_draft
    # self-gates — it only yields tasks when a real plan exists, so a clarifying turn attaches
    # nothing. Best-effort: a failed/empty draft just means no button — the prose synthesis posts.
    attachments: list[dict[str, Any]] | None = None
    plan_presented = directive.intent != PMIntent.ASK_USER and bool(synthesis.strip())
    if plan_presented:
        attachments = await _resolve_plan_attachment(model, state, contributions, conversation_id)
    if synthesis.strip():
        await _emit(synthesis, default_agent_id, attachments)
        published = True
    # The PM presented a plan in prose but the structured draft stayed empty even
    # after the retry: don't leave the user with a "listo" message and no
    # "Generar Plan" button — tell them so they can retry, instead of silence.
    if plan_presented and attachments is None:
        await _system_notice(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            mode=mode,
            content=(
                "⚠️ El equipo preparó el plan, pero no pude estructurarlo automáticamente "
                "para el botón «Generar Plan». Reintenta enviando el mensaje de nuevo "
                "(o escribe «genera el plan»)."
            ),
            redis=redis,
        )
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

            # c9 (audit 2026-07-03): idempotency guard. If the conversation's LATEST
            # message is already a reply (agent/system), this user turn was answered —
            # by the original detached task or a prior resume() — so skip. This makes
            # respond_to_conversation safe to call repeatedly, which the durability
            # sweep (resume_pending_replies) relies on to never double-reply.
            latest_kind = (
                await session.execute(
                    select(Message.author_kind)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.tenant_id == tenant_id,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest_kind is not None and latest_kind != "user":
                return

            effective = await resolve_chat_model_config(session, project)
            temperature = float(effective.get("temperature", _DEFAULT_TEMPERATURE))
            provider, kind, api_model = await _resolve_chat_provider(session, effective, vault)
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
            # Ground the planning in the project's existing state (prior plans,
            # project memories, docs/code via RAG) — provider-agnostic context, not an
            # agent browsing the repo. The latest USER message drives the doc retrieval.
            # P0-5: con el embedder real el retrieval es híbrido (BM25+vector) y el
            # agent_id del PM une sus KBs de rol; ambos best-effort (Ollama caído →
            # BM25-only dentro de rag_search).
            latest_user_text = next(
                (m.content for m in reversed(list(rows)) if m.author_kind == "user"), ""
            )
            query_embedder = OllamaEmbedder()
            try:
                project_context = await build_project_context(
                    session,
                    project,
                    latest_user_text,
                    agent_id=default_agent_id,
                    embedder=query_embedder,
                )
            finally:
                with contextlib.suppress(Exception):
                    await query_embedder.aclose()
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
        if kind == "claude_sdk" and extra.pop("effort", None) is not None:
            # Claude Code (claude_sdk) is AGENTIC: extended thinking on the chat's
            # structured planning calls makes it blow past its internal turn budget
            # ("Reached maximum number of turns (8)"). The interactive chat therefore
            # runs claude_sdk WITHOUT extended reasoning (measured: pm_decide ~8s vs
            # failing). Non-agentic providers (ollama/azure/copilot) keep the configured
            # reasoning. For deep reasoning in the chat, use a non-agentic provider.
            _log.info("chat.claude_sdk_reasoning_dropped", conversation_id=str(conversation_id))
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


# c9 (audit 2026-07-03): durability of the chat turn. The team reply runs as a
# DETACHED in-process task (schedule_reply), so a restart mid-turn drops it. A
# startup sweep resumes turns left unanswered. Only STALE turns (older than this)
# are resumed — a fresh turn is still being handled in-process.
_RESUME_STALE_SECONDS = 30
_RESUME_SWEEP_LOCK = "chat:resume:pending"
_RESUME_MAX = 200


async def resume_pending_replies(*, vault: LLMProviderVaultStore | None, redis: Redis) -> int:
    """Resume chat turns left unanswered when a previous api-server process died (c9).

    The user's message is durable; the team reply is not (it runs detached in-process).
    At startup this finds every conversation whose LATEST message is a user message
    older than ``_RESUME_STALE_SECONDS`` and re-schedules the reply.
    ``respond_to_conversation`` is idempotent (it skips an already-answered
    conversation), so a redundant resume is a no-op. A redis single-flight lock keeps
    multiple uvicorn workers from all sweeping. Best-effort — never raises. Returns the
    number of conversations resumed.
    """
    try:
        got_lock = await redis.set(_RESUME_SWEEP_LOCK, b"1", nx=True, ex=300)
    except Exception:  # redis down — skip rather than risk an unbounded resume storm
        _log.warning("chat.resume_sweep_no_redis")
        return 0
    if not got_lock:
        return 0

    older_than = datetime.now(tz=UTC) - timedelta(seconds=_RESUME_STALE_SECONDS)
    later = aliased(Message)
    sm = get_admin_sessionmaker()
    try:
        async with sm() as session:
            rows = (
                await session.execute(
                    select(Message.conversation_id, Message.tenant_id, Message.mode)
                    .where(
                        Message.author_kind == "user",
                        Message.created_at < older_than,
                        ~exists().where(
                            and_(
                                later.conversation_id == Message.conversation_id,
                                later.created_at > Message.created_at,
                            )
                        ),
                    )
                    .limit(_RESUME_MAX)
                )
            ).all()
    except Exception as exc:  # a query failure must not stop api-server startup
        _log.warning("chat.resume_sweep_query_failed", error=str(exc))
        return 0

    resumed = 0
    for conversation_id, tenant_id, mode in rows:
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
        resumed += 1
    if resumed:
        _log.info("chat.resumed_pending_replies", count=resumed)
    return resumed


__all__ = [
    "build_chat_provider",
    "history_from_messages",
    "planning_roles_from_strings",
    "resolve_chat_model_config",
    "respond_to_conversation",
    "resume_pending_replies",
    "schedule_reply",
    "team_planning_roles",
]
