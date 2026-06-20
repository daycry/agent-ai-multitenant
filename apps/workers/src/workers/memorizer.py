"""Celery wrapper around the Memorizer pipeline (Plan 04.5 task_04_5_02).

Plan 04 task_04_03 built the three pure pieces of the Memorizer:

  * :func:`api_server.memorizer.should_memorize` — the gate.
  * :func:`api_server.memorizer.distil_execution` — the LLM step.
  * :func:`api_server.memorizer.persist_memory_candidates` — the writer.

This module wires them into a Celery task that fires *after* an
`Execution` finishes. The trigger lives in
:func:`workers.execution.conduct_execution`: as soon as
:func:`api_server.db.execution_repo.finalize_execution` returns with
``status == "done"``, the executor enqueues
``workers.memorize_execution`` so the LLM call doesn't block the
agent's hand-off.

The task is intentionally tolerant: a Memorizer failure must never
break the executor's record of the run. Errors are logged and the
task returns a JSON-safe dict so Celery's result backend keeps a
trace.

Scope mapping for AI agents:

  - ``team_shared``    — owner = the project's ``team_id`` (skipped if
                         the project has no team yet).
  - ``project_shared`` — owner = the task's ``project_id``.
  - ``global``         — no owner pointer (tenant-wide).
  - ``private``        — there is no clean "owner user" for an AI
                         agent's auto-distilled memories (Agent has no
                         created_by); the task logs and skips. Human
                         "remember this" memories use ``POST /memories``
                         which DOES carry an authenticated user.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from api_server.db.domain import Agent, HumanWorkSession, MemoryScope, Project, Task, Team
from api_server.db.execution_repo import get_execution
from api_server.db.models import User
from api_server.db.platform_settings import get_default_memory_scope, get_memorizable_statuses
from api_server.memorizer import (
    distil_execution,
    distil_human_work_session,
    persist_memory_candidates,
    should_memorize,
    should_memorize_human_session,
)
from api_server.memorizer.policy import (
    MemorizeSkipReason,
    resolve_effective_memory_scope,
    route_scope_for_type,
)
from shared_llm.base import LLMProvider
from shared_llm.providers import OllamaProvider
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

if TYPE_CHECKING:
    from api_server.ingestion.embeddings import Embedder

_log = structlog.get_logger("workers.memorizer")

# Type alias for the LLM factory tests can override. Returns the
# provider AND a flag whether the caller owns it (for `aclose()`).
LLMFactory = Callable[[Settings], LLMProvider]

# Factory del embedder que rellena ``memory_entries.embedding`` al persistir las
# memorias destiladas (Plan 06.17 task_06_17_03). Best-effort: si el embed falla
# la fila nace NULL y el worker de back-fill la rellena luego. Los tests lo
# sobreescriben con un HashEmbedder; ``None`` deshabilita el embed-al-crear.
EmbedderFactory = Callable[[Settings], "Embedder"]


def _default_embedder_factory(settings: Settings) -> Embedder:
    """Embedder por defecto del Memorizer: el ``OllamaEmbedder`` de la ingesta,
    apuntado a la URL de Ollama del worker (mismo modelo / dimensión)."""
    from api_server.ingestion.embeddings import OllamaEmbedder

    return OllamaEmbedder(base_url=settings.memory_embedder_base_url)


def _default_llm_factory(settings: Settings) -> LLMProvider:
    """Default provider: Ollama on the URL the workers Settings carries.

    The Memorizer doesn't need a powerful model — a small local one
    is the right trade-off (cheap, no quota, no egress). Override via
    ``WORKERS_MEMORIZER_LLM_BASE_URL`` / ``WORKERS_MEMORIZER_LLM_MODEL``.
    """
    return OllamaProvider(
        base_url=settings.memorizer_llm_base_url,
        default_model=settings.memorizer_llm_model,
    )


@app.task(name="workers.memorize_execution")  # type: ignore[misc]
def memorize_execution(execution_id: str) -> dict[str, Any]:
    """Celery entry point. Run the Memorizer for one finished Execution.

    Returns a dict so the result backend keeps a useful breadcrumb:

      {"execution_id": ..., "persisted": int, "reason": "ok"|"skipped:..."}
    """
    settings = get_settings()
    return asyncio.run(
        _memorize_execution_async(
            UUID(execution_id),
            settings=settings,
            llm_factory=_default_llm_factory,
            embedder_factory=_default_embedder_factory,
        )
    )


async def _memorize_execution_async(
    execution_id: UUID,
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    embedder_factory: EmbedderFactory | None = None,
) -> dict[str, Any]:
    """Async core. Tests inject a fake `llm_factory` to avoid the
    network; ``embedder_factory`` es opcional (best-effort embed-al-crear)."""
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            ctx = await _load_context(session, execution_id)
        if ctx is None:
            return _result(execution_id, 0, "skipped:execution_not_found")

        # Estados elegibles operator-configurable (Plan 06.17 task_06_17_04): el
        # gate ya no asume "solo done"; el operador puede añadir p.ej. 'aborted'.
        async with sessionmaker() as settings_session:
            eligible = await get_memorizable_statuses(settings_session)
            platform_default_scope = await get_default_memory_scope(settings_session)

        # ADR 0071: scope efectivo = equipo del proyecto > agente > default plataforma.
        effective_scope = resolve_effective_memory_scope(
            ctx["team_memory_scope"],
            ctx["agent"]["memory_scope"],
            platform_default_scope,
        )

        decision = should_memorize(
            status=ctx["execution"]["status"],
            memory_scope=effective_scope,
            eligible_statuses=eligible,
        )
        if not decision.memorise:
            # Persistir el CÓDIGO del motivo (no solo log) para que la UI lo
            # consulte. Fin del skip silencioso para agentes IA.
            await _record_skip_reason(sessionmaker, execution_id, decision.code)
            _log.info(
                "memorizer.skipped",
                execution_id=str(execution_id),
                reason=decision.reason,
                code=decision.code.value,
            )
            return _result(execution_id, 0, f"skipped:{decision.reason}")

        # ADR 0071: owner por scope ENRUTADO (semantic→efectivo; episodic→acotado a
        # project_shared). Skip temprano (antes de distilar) solo si NINGÚN scope
        # posible tiene owner (p.ej. private IA, o sin equipo y sin proyecto).
        owners, last_skip = _resolve_routed_owners(
            effective_scope,
            lambda s: _resolve_owner(
                scope=s, project=ctx["project"], task_project_id=ctx["task"]["project_id"]
            ),
        )
        if not owners:
            await _record_skip_reason(sessionmaker, execution_id, last_skip)
            _log.info(
                "memorizer.skipped",
                execution_id=str(execution_id),
                reason="no_owner_for_scope",
                code=last_skip.value,
            )
            return _result(execution_id, 0, f"skipped:{last_skip.value}")

        llm = llm_factory(settings)
        try:
            candidates = await distil_execution(
                execution=ctx["execution"], agent=ctx["agent"], llm=llm
            )
        finally:
            await llm.aclose()

        if not candidates:
            await _record_skip_reason(sessionmaker, execution_id, MemorizeSkipReason.LLM_EMPTY)
            return _result(execution_id, 0, "ok:no_candidates")

        embedder = embedder_factory(settings) if embedder_factory is not None else None
        try:
            total = await _persist_routed(
                sessionmaker,
                candidates,
                effective_scope=effective_scope,
                owners=owners,
                tenant_id=ctx["tenant_id"],
                agent_id=ctx["agent"]["id"],
                extra_metadata={"distill_model": getattr(llm, "name", "unknown")},
                embedder=embedder,
                source_execution_id=execution_id,
            )
        finally:
            if embedder is not None:
                await embedder.aclose()
        # Memorizó OK → cualquier motivo de skip de un intento previo se limpia.
        await _record_skip_reason(sessionmaker, execution_id, None)
        _log.info(
            "memorizer.persisted",
            execution_id=str(execution_id),
            count=total,
            scope=effective_scope,
        )
        return _result(execution_id, total, "ok")
    except Exception as exc:
        # Belt + braces: a Memorizer failure must never propagate up
        # into the Celery worker and crash the run pipeline.
        _log.exception("memorizer.failed", execution_id=str(execution_id), error=str(exc))
        return _result(execution_id, 0, f"error:{exc}")
    finally:
        await engine.dispose()


async def _load_context(session: AsyncSession, execution_id: UUID) -> dict[str, Any] | None:
    """Pull Execution + Agent + Task + Project in one place.

    Returns the dict the Memorizer pipeline expects, or None if the
    Execution row vanished between trigger and pickup (unlikely but
    possible: races, manual deletes).
    """
    execution = await get_execution(session, execution_id)
    if execution is None or execution.agent_id is None:
        return None
    agent = await session.get(Agent, execution.agent_id)
    if agent is None:
        return None
    task = await session.get(Task, execution.task_id)
    if task is None:
        return None
    project = await session.get(Project, task.project_id)
    # ADR 0071: la política de memoria la gobierna el equipo del proyecto.
    team_memory_scope: str | None = None
    if project is not None and project.team_id is not None:
        team = await session.get(Team, project.team_id)
        team_memory_scope = team.memory_scope if team is not None else None
    return {
        "tenant_id": execution.tenant_id,
        "execution": {
            "status": execution.status,
            "output": execution.output,
            "steps_log": execution.steps_log,
            "task_title": task.title,
        },
        "agent": {
            "id": agent.id,
            "role": agent.role,
            "memory_scope": agent.memory_scope,
        },
        "task": {
            "id": task.id,
            "project_id": task.project_id,
        },
        "project": (
            {"id": project.id, "team_id": project.team_id} if project is not None else None
        ),
        "team_memory_scope": team_memory_scope,
    }


def _resolve_owner(
    *,
    scope: str,
    project: Mapping[str, Any] | None,
    task_project_id: UUID,
) -> tuple[dict[str, UUID | None] | None, MemorizeSkipReason]:
    """Compute the owner kwargs `persist_memory_candidates` needs.

    Returns ``(owner, skip_code)``: ``owner`` is None when the scope is
    ``private`` (no user attribution for auto-distilled AI memories — handled by
    ``POST /memories`` instead) or when ``team_shared`` is requested but the
    project has no team yet; ``skip_code`` is the canonical
    :class:`MemorizeSkipReason` to persist in that case so the operator can tell
    from the UI why a run didn't leave memory (Plan 06.17 task_06_17_04). On a
    resolvable owner ``skip_code`` is ``OK`` and is ignored by the caller.
    """
    if scope == MemoryScope.GLOBAL.value:
        return {"user_id": None, "team_id": None, "project_id": None}, MemorizeSkipReason.OK
    if scope == MemoryScope.PROJECT_SHARED.value:
        return (
            {"user_id": None, "team_id": None, "project_id": task_project_id},
            MemorizeSkipReason.OK,
        )
    if scope == MemoryScope.TEAM_SHARED.value:
        if project is None or project.get("team_id") is None:
            _log.info("memorizer.skipped_team_shared_without_team")
            return None, MemorizeSkipReason.NO_TEAM
        return (
            {"user_id": None, "team_id": project["team_id"], "project_id": None},
            MemorizeSkipReason.OK,
        )
    # MemoryScope.PRIVATE or anything non-canonical — already filtered
    # by `should_memorize`, but reaching here means private. AI agents
    # have no created_by; the human POST /memories path covers private.
    _log.info("memorizer.skipped_private_scope_for_ai_agent")
    return None, MemorizeSkipReason.SKIP_PRIVATE


def _resolve_routed_owners(
    effective_scope: str,
    owner_resolver: Callable[[str], tuple[dict[str, UUID | None] | None, MemorizeSkipReason]],
) -> tuple[dict[str, dict[str, UUID | None]], MemorizeSkipReason]:
    """ADR 0071: resuelve el owner de cada scope ENRUTADO (semantic→efectivo;
    episodic→acotado a project_shared) vía ``owner_resolver`` (distinto para IA y
    humano). Devuelve ``(owners_por_scope, last_skip)``; ``owners`` vacío ⇒ ningún
    scope posible tiene owner (skip total)."""
    owners: dict[str, dict[str, UUID | None]] = {}
    last_skip = MemorizeSkipReason.OK
    for routed in (
        route_scope_for_type(effective_scope, "semantic"),
        route_scope_for_type(effective_scope, "episodic"),
    ):
        if routed in owners:
            continue
        owner, skip = owner_resolver(routed)
        if owner is not None:
            owners[routed] = owner
        else:
            last_skip = skip
    return owners, last_skip


async def _persist_routed(
    sessionmaker: async_sessionmaker[AsyncSession],
    candidates: Sequence[Any],
    *,
    effective_scope: str,
    owners: Mapping[str, dict[str, UUID | None]],
    tenant_id: UUID,
    agent_id: UUID | None,
    extra_metadata: dict[str, Any],
    embedder: Any,
    source_execution_id: UUID | None = None,
    source_human_work_session_id: UUID | None = None,
) -> int:
    """ADR 0071: agrupa candidatos por scope enrutado y persiste cada grupo con su
    owner; un grupo cuyo scope no tiene owner (p.ej. semantic team_shared sin
    equipo) se omite. Devuelve el total persistido."""
    groups: dict[str, list[Any]] = {}
    for cand in candidates:
        groups.setdefault(route_scope_for_type(effective_scope, cand.type), []).append(cand)
    total = 0
    for scope, group in groups.items():
        owner = owners.get(scope)
        if owner is None:
            continue
        async with sessionmaker() as session, session.begin():
            rows = await persist_memory_candidates(
                session,
                group,
                tenant_id=tenant_id,
                scope=scope,
                agent_id=agent_id,
                source_execution_id=source_execution_id,
                source_human_work_session_id=source_human_work_session_id,
                extra_metadata=extra_metadata,
                embedder=embedder,
                **owner,
            )
        total += len(rows)
    return total


async def _record_skip_reason(
    sessionmaker: async_sessionmaker[AsyncSession],
    execution_id: UUID,
    code: MemorizeSkipReason | None,
) -> None:
    """Persistir el motivo de no-memorización en ``executions.memorize_skip_reason``.

    ``code=None`` lo limpia (NULL) tras una memorización OK. Best-effort + en su
    propia transacción: un fallo escribiendo el motivo NUNCA debe propagar (el
    Memorizer es tolerante a fallos por diseño). El worker corre con un rol
    BYPASSRLS, así que el UPDATE por id no necesita ``app.tenant_id``."""
    value = code.value if code is not None else None
    try:
        async with sessionmaker() as session, session.begin():
            await session.execute(
                sa_text("UPDATE executions SET memorize_skip_reason = :reason WHERE id = :eid"),
                {"reason": value, "eid": execution_id},
            )
    except Exception as exc:  # pragma: no cover - defensivo
        _log.warning(
            "memorizer.skip_reason_write_failed",
            execution_id=str(execution_id),
            error=str(exc),
        )


def _result(execution_id: UUID, persisted: int, reason: str) -> dict[str, Any]:
    return {
        "execution_id": str(execution_id),
        "persisted": persisted,
        "reason": reason,
    }


# =============================================================================
# Human work session memorizer (Plan 16 task_16_15)
# =============================================================================
# A HumanWorkSession replaces Execution for agent_type='human' tasks. When such
# a task reaches `done` (auto_approve submit, or a peer reviewer's approval) the
# inbox/review endpoint enqueues `workers.memorize_human_work_session` so the
# Memorizer distils the human's deliverable into MemoryEntries — useful for
# future plans ("user X made decision D in context C, which led to outcome O"),
# cited back at the HumanWorkSession via source_human_work_session_id.
#
# Scope mapping for human agents differs from AI agents in ONE place: `private`
# DOES resolve to an owner — the human who did the work (the work session's
# user_id) — because a human task HAS a clean user attribution (an AI agent
# does not, which is why _resolve_owner skips private for AI).


@app.task(name="workers.memorize_human_work_session")  # type: ignore[misc]
def memorize_human_work_session(work_session_id: str) -> dict[str, Any]:
    """Celery entry point. Run the Memorizer for one finished HumanWorkSession.

    Returns a dict so the result backend keeps a useful breadcrumb:

      {"work_session_id": ..., "persisted": int, "reason": "ok"|"skipped:..."}
    """
    settings = get_settings()
    return asyncio.run(
        _memorize_human_work_session_async(
            UUID(work_session_id),
            settings=settings,
            llm_factory=_default_llm_factory,
            embedder_factory=_default_embedder_factory,
        )
    )


async def _memorize_human_work_session_async(
    work_session_id: UUID,
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    embedder_factory: EmbedderFactory | None = None,
) -> dict[str, Any]:
    """Async core. Tests inject a fake `llm_factory` to avoid the network;
    ``embedder_factory`` es opcional (best-effort embed-al-crear)."""
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            ctx = await _load_human_context(session, work_session_id)
        if ctx is None:
            return _human_result(work_session_id, 0, "skipped:work_session_not_found")

        # ADR 0071: scope efectivo = equipo del proyecto > agente > default plataforma.
        async with sessionmaker() as settings_session:
            platform_default_scope = await get_default_memory_scope(settings_session)
        effective_scope = resolve_effective_memory_scope(
            ctx["team_memory_scope"],
            ctx["agent"]["memory_scope"] if ctx["agent"] else None,
            platform_default_scope,
        )

        decision = should_memorize_human_session(
            task_status=ctx["task"]["status"],
            memory_scope=effective_scope,
        )
        if not decision.memorise:
            _log.info(
                "memorizer.human_skipped",
                work_session_id=str(work_session_id),
                reason=decision.reason,
            )
            return _human_result(work_session_id, 0, f"skipped:{decision.reason}")

        # ADR 0071: owner por scope enrutado, con la resolución HUMANA (atribuye
        # user en private, a diferencia de la IA).
        owners, _ = _resolve_routed_owners(
            effective_scope,
            lambda s: (
                _resolve_human_owner(
                    scope=s,
                    session_user_id=ctx["session"]["user_id"],
                    project=ctx["project"],
                    task_project_id=ctx["task"]["project_id"],
                ),
                MemorizeSkipReason.OK,
            ),
        )
        if not owners:
            return _human_result(work_session_id, 0, "skipped:no_owner_for_scope")

        llm = llm_factory(settings)
        try:
            candidates = await distil_human_work_session(
                session=ctx["session"],
                agent=ctx["agent"],  # type: ignore[arg-type]
                user=ctx["user"],
                llm=llm,
            )
        finally:
            await llm.aclose()

        if not candidates:
            return _human_result(work_session_id, 0, "ok:no_candidates")

        embedder = embedder_factory(settings) if embedder_factory is not None else None
        try:
            total = await _persist_routed(
                sessionmaker,
                candidates,
                effective_scope=effective_scope,
                owners=owners,
                tenant_id=ctx["tenant_id"],
                agent_id=ctx["agent"]["id"] if ctx["agent"] else None,
                extra_metadata={
                    "distill_model": getattr(llm, "name", "unknown"),
                    "source_kind": "human_work_session",
                    "task_id": str(ctx["task"]["id"]),
                    "worker_user_id": (
                        str(ctx["session"]["user_id"]) if ctx["session"]["user_id"] else None
                    ),
                },
                embedder=embedder,
                source_human_work_session_id=work_session_id,
            )
        finally:
            if embedder is not None:
                await embedder.aclose()
        _log.info(
            "memorizer.human_persisted",
            work_session_id=str(work_session_id),
            count=total,
            scope=effective_scope,
        )
        return _human_result(work_session_id, total, "ok")
    except Exception as exc:
        # Belt + braces: a Memorizer failure must never propagate up and crash
        # the worker that picked up the task.
        _log.exception(
            "memorizer.human_failed", work_session_id=str(work_session_id), error=str(exc)
        )
        return _human_result(work_session_id, 0, f"error:{exc}")
    finally:
        await engine.dispose()


async def _load_human_context(
    session: AsyncSession, work_session_id: UUID
) -> dict[str, Any] | None:
    """Pull HumanWorkSession + Task + (human) Agent + Project + User.

    Returns the dict the human Memorizer pipeline expects, or None if the work
    session row vanished. The human Agent is the task's ``assigned_agent_id``
    (set when the orchestrator routed the human task); its ``memory_scope``
    drives the gate + owner resolution exactly like an AI agent's does.
    """
    work_session = await session.get(HumanWorkSession, work_session_id)
    if work_session is None:
        return None
    task = await session.get(Task, work_session.task_id)
    if task is None:
        return None
    agent = (
        await session.get(Agent, task.assigned_agent_id)
        if task.assigned_agent_id is not None
        else None
    )
    project = await session.get(Project, task.project_id)
    # ADR 0071: la política de memoria la gobierna el equipo del proyecto.
    team_memory_scope: str | None = None
    if project is not None and project.team_id is not None:
        team = await session.get(Team, project.team_id)
        team_memory_scope = team.memory_scope if team is not None else None
    user = (
        await session.get(User, work_session.user_id) if work_session.user_id is not None else None
    )
    return {
        "tenant_id": work_session.tenant_id,
        "team_memory_scope": team_memory_scope,
        "session": {
            "id": work_session.id,
            "user_id": work_session.user_id,
            "comments": work_session.comments,
            "hours_logged": (
                str(work_session.hours_logged) if work_session.hours_logged is not None else None
            ),
            "output_files_attached": work_session.output_files_attached,
            "task_title": task.title,
        },
        "task": {
            "id": task.id,
            "status": task.status,
            "project_id": task.project_id,
        },
        "agent": (
            {
                "id": agent.id,
                "role": agent.role,
                "memory_scope": agent.memory_scope,
            }
            if agent is not None
            else None
        ),
        "project": (
            {"id": project.id, "team_id": project.team_id} if project is not None else None
        ),
        "user": {"name": (user.full_name or user.email) if user is not None else None},
    }


def _resolve_human_owner(
    *,
    scope: str,
    session_user_id: UUID | None,
    project: Mapping[str, Any] | None,
    task_project_id: UUID,
) -> dict[str, UUID | None] | None:
    """Compute the owner kwargs `persist_memory_candidates` needs for a human
    session.

    Differs from :func:`_resolve_owner` (the AI path) only for ``private``: a
    human task HAS a clean user attribution (the work session's ``user_id`` =
    the human who did the work), so ``private`` memories are owned by that user
    rather than skipped. Returns None when ``team_shared`` is requested but the
    project has no team, or ``private`` is requested but the session has no
    user (deleted before distillation).
    """
    if scope == MemoryScope.GLOBAL.value:
        return {"user_id": None, "team_id": None, "project_id": None}
    if scope == MemoryScope.PROJECT_SHARED.value:
        return {"user_id": None, "team_id": None, "project_id": task_project_id}
    if scope == MemoryScope.TEAM_SHARED.value:
        team_id = project.get("team_id") if project is not None else None
        if team_id is None:
            _log.info("memorizer.human_skipped_team_shared_without_team")
            return None
        return {"user_id": None, "team_id": team_id, "project_id": None}
    # PRIVATE — a human task HAS a clean user attribution (unlike an AI agent):
    # the work session's user_id is the human who did the work. Any other
    # (non-canonical) scope is already filtered by should_memorize_human_session
    # and falls through here as a no-owner skip.
    if scope == MemoryScope.PRIVATE.value and session_user_id is not None:
        return {"user_id": session_user_id, "team_id": None, "project_id": None}
    _log.info("memorizer.human_skipped_no_owner", scope=scope)
    return None


def _human_result(work_session_id: UUID, persisted: int, reason: str) -> dict[str, Any]:
    return {
        "work_session_id": str(work_session_id),
        "persisted": persisted,
        "reason": reason,
    }


def trigger_memorize_human_work_session(work_session_id: UUID, task_status: str) -> bool:
    """Enqueue the human Memorizer for one HumanWorkSession. Returns True if the
    task was enqueued, False if the task status didn't warrant it.

    Called by the inbox submit (``auto_approve``) and the peer-review approve
    endpoints right after the Task reaches ``done``. Failures (broker down,
    etc.) are swallowed and logged so a Memorizer-side problem can never roll
    back the human's delivery.
    """
    if task_status != "done":
        return False
    try:
        memorize_human_work_session.apply_async(args=[str(work_session_id)], queue="default")
    except Exception as exc:
        _log.warning(
            "memorizer.human_enqueue_failed",
            work_session_id=str(work_session_id),
            error=str(exc),
        )
        return False
    return True


# Terminal ExecutionStatus values (api_server.db.domain.ExecutionStatus). The
# trigger hands ANY finished execution to the Memorizer; WHICH terminal statuses
# are actually memorised is the operator-configurable gate in the task
# (should_memorize + get_memorizable_statuses, default {"done"}). The old
# hardcoded `status == "done"` here pre-filtered BEFORE that gate, so the operator
# setting was unreachable for error statuses — agents could not learn from
# aborted/failed runs (06.17 added the setting but the trigger shadowed it).
# Gating on terminal-ness (not the policy) fixes that and keeps this
# fire-and-forget path free of DB reads.
_TERMINAL_STATUSES = frozenset({"done", "failed", "aborted", "cancelled"})


def trigger_memorize(execution_id: UUID, status: str) -> bool:
    """Enqueue the Memorizer for one FINISHED Execution. Returns True if the task
    was enqueued, False if the status is non-terminal (mid-execution). The task
    applies the operator-configured ``memorizable_statuses`` gate, so an error
    status is only actually memorised when the operator opted in.

    Imported by :mod:`workers.execution` right after `finalize_execution`.
    Failures (broker down, etc.) are swallowed and logged so a
    Memorizer-side problem can never bring down an agent run.
    """
    if status not in _TERMINAL_STATUSES:
        return False
    try:
        memorize_execution.apply_async(args=[str(execution_id)], queue="default")
    except Exception as exc:
        # Broker down / serialisation glitch — log and pretend the
        # trigger never happened. The executor must finish its hand-off.
        _log.warning(
            "memorizer.enqueue_failed",
            execution_id=str(execution_id),
            error=str(exc),
        )
        return False
    return True


__all__ = [
    "memorize_execution",
    "memorize_human_work_session",
    "trigger_memorize",
    "trigger_memorize_human_work_session",
]
