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
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

import structlog
from api_server.db.domain import Agent, MemoryScope, Project, Task
from api_server.db.execution_repo import get_execution
from api_server.memorizer import (
    distil_execution,
    persist_memory_candidates,
    should_memorize,
)
from shared_llm.base import LLMProvider
from shared_llm.providers import OllamaProvider
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.memorizer")

# Type alias for the LLM factory tests can override. Returns the
# provider AND a flag whether the caller owns it (for `aclose()`).
LLMFactory = Callable[[Settings], LLMProvider]


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
        )
    )


async def _memorize_execution_async(
    execution_id: UUID,
    *,
    settings: Settings,
    llm_factory: LLMFactory,
) -> dict[str, Any]:
    """Async core. Tests inject a fake `llm_factory` to avoid the
    network."""
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            ctx = await _load_context(session, execution_id)
        if ctx is None:
            return _result(execution_id, 0, "skipped:execution_not_found")

        decision = should_memorize(
            status=ctx["execution"]["status"],
            memory_scope=ctx["agent"]["memory_scope"],
        )
        if not decision.memorise:
            _log.info(
                "memorizer.skipped",
                execution_id=str(execution_id),
                reason=decision.reason,
            )
            return _result(execution_id, 0, f"skipped:{decision.reason}")

        owner = _resolve_owner(
            scope=ctx["agent"]["memory_scope"],
            project=ctx["project"],
            task_project_id=ctx["task"]["project_id"],
        )
        if owner is None:
            return _result(execution_id, 0, "skipped:no_owner_for_scope")

        llm = llm_factory(settings)
        try:
            candidates = await distil_execution(
                execution=ctx["execution"], agent=ctx["agent"], llm=llm
            )
        finally:
            await llm.aclose()

        if not candidates:
            return _result(execution_id, 0, "ok:no_candidates")

        async with sessionmaker() as session, session.begin():
            rows = await persist_memory_candidates(
                session,
                candidates,
                tenant_id=ctx["tenant_id"],
                scope=ctx["agent"]["memory_scope"],
                agent_id=ctx["agent"]["id"],
                source_execution_id=execution_id,
                extra_metadata={"distill_model": getattr(llm, "name", "unknown")},
                **owner,
            )
        _log.info(
            "memorizer.persisted",
            execution_id=str(execution_id),
            count=len(rows),
            scope=ctx["agent"]["memory_scope"],
        )
        return _result(execution_id, len(rows), "ok")
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
    }


def _resolve_owner(
    *,
    scope: str,
    project: Mapping[str, Any] | None,
    task_project_id: UUID,
) -> dict[str, UUID | None] | None:
    """Compute the owner kwargs `persist_memory_candidates` needs.

    Returns None when the scope is `private` (no user attribution for
    auto-distilled AI memories — handled by `POST /memories` instead)
    or when `team_shared` is requested but the project has no team yet.
    """
    if scope == MemoryScope.GLOBAL.value:
        return {"user_id": None, "team_id": None, "project_id": None}
    if scope == MemoryScope.PROJECT_SHARED.value:
        return {"user_id": None, "team_id": None, "project_id": task_project_id}
    if scope == MemoryScope.TEAM_SHARED.value:
        if project is None or project.get("team_id") is None:
            _log.info("memorizer.skipped_team_shared_without_team")
            return None
        return {"user_id": None, "team_id": project["team_id"], "project_id": None}
    # MemoryScope.PRIVATE or anything non-canonical — already filtered
    # by `should_memorize`, but reaching here means private. AI agents
    # have no created_by; the human POST /memories path covers private.
    _log.info("memorizer.skipped_private_scope_for_ai_agent")
    return None


def _result(execution_id: UUID, persisted: int, reason: str) -> dict[str, Any]:
    return {
        "execution_id": str(execution_id),
        "persisted": persisted,
        "reason": reason,
    }


def trigger_memorize(execution_id: UUID, status: str) -> bool:
    """Enqueue the Memorizer for one Execution. Returns True if the
    task was enqueued, False if the status didn't warrant it.

    Imported by :mod:`workers.execution` right after `finalize_execution`.
    Failures (broker down, etc.) are swallowed and logged so a
    Memorizer-side problem can never bring down an agent run.
    """
    if status != "done":
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
    "trigger_memorize",
]
