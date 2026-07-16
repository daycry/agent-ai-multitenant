"""Memorizer gating policy (Plan 04 task_04_03).

Decides whether a finished `Execution` should produce memories. Two
inputs:

  - **Execution status** — the platform only memorises *successful*
    runs (``done``). An aborted/failed run is not a positive example
    to remember; if anything its failure is recorded elsewhere
    (logs, audit). We may revisit this when we add "lessons from
    failures" — but for v1 it's an explicit choice to keep the
    memory store noise-free.
  - **Agent memory scope** — every agent declares one of four scopes
    (`private` / `team_shared` / `project_shared` / `global`). If a
    deployment ever needs to fully disable memory for a specific
    agent the value can be set to a free-form custom string at the
    DB level (e.g. ``"none"``); anything outside the four canonical
    scopes is treated as "do not memorise".

The decision is returned as a dataclass so the caller (the Celery
task) can log both the verdict and the reason in one shot.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass

from api_server.db.domain import ExecutionStatus, MemoryScope

# The four canonical scopes that mean "yes, persist memories".
_MEMORISABLE_SCOPES: frozenset[str] = frozenset(s.value for s in MemoryScope)

# Default eligible execution statuses when the operator hasn't overridden the
# `memory.memorizable_statuses` platform setting (Plan 06.17 task_06_17_04). Kept
# here so the pure policy has a sensible default even when called without the
# operator-resolved set (e.g. the human-session path / tests).
# P1-1 (investigación 2026-07-11): los FRACASOS también dejan lección por
# defecto — un run failed/aborted/needs_human_review es de lo más informativo
# (el callejón sin salida a evitar). Antes solo `done` aprendía; el operador
# puede seguir estrechando el set vía la platform setting.
_DEFAULT_ELIGIBLE_STATUSES: frozenset[str] = frozenset(
    {
        ExecutionStatus.DONE.value,
        ExecutionStatus.FAILED.value,
        ExecutionStatus.ABORTED.value,
        "needs_human_review",
    }
)


class MemorizeSkipReason(enum.StrEnum):
    """Código canónico del motivo por el que NO se memorizó (Plan 06.17
    task_06_17_04).

    Hasta esta tarea el motivo de skip vivía SOLO en los logs (``policy.py`` /
    ``workers/memorizer.py``), invisible para la UI. Ahora el worker persiste uno
    de estos códigos cortos y estables en ``executions.memorize_skip_reason``,
    que un endpoint expone para que el operador entienda por qué un run no dejó
    memoria. ``OK`` no es un motivo de skip (la columna queda NULL); se incluye
    para que la decisión lo lleve siempre y el caller no tenga que mapear None.
    """

    OK = "ok"
    NOT_DONE = "not_done"  # el estado de la ejecución no es elegible
    SKIP_PRIVATE = "skip_private"  # agente IA con scope private (sin owner user)
    NO_TEAM = "no_team"  # scope team_shared pero el proyecto no tiene equipo
    NO_SCOPE = "no_scope"  # memory_scope NULL / no canónico (opt-out)
    # F2.3 (auditoría 2026-07-02): llm_empty conflataba tres causas distintas y
    # hacía indiagnosticables casos como el run done 019f1dcd. Ahora se separan:
    LLM_EMPTY = "llm_empty"  # el LLM decidió que no hay nada que recordar (legítimo)
    LLM_ERROR = "llm_error"  # la llamada al LLM falló (provider caído/timeout)
    LLM_UNPARSEABLE = "llm_unparseable"  # el LLM respondió pero sin JSON parseable
    # AUD16-21: el run lo cerró un finalizador ADMINISTRATIVO (sweeper de
    # zombis, supersede por re-entrega, cancel, soft-timeout) — ese camino no
    # pasa por el memorizer y antes dejaba la columna NULL (indistinguible de
    # un bug del trigger).
    ADMINISTRATIVE_FINALIZE = "administrative_finalize"


@dataclass(frozen=True)
class MemorizeDecision:
    """Outcome of `should_memorize`.

    ``memorise`` is what the caller branches on. ``reason`` is the human-readable
    string we log when ``memorise`` is False; ``code`` is the canonical
    :class:`MemorizeSkipReason` the worker persists on the execution so the
    operator can tell from the UI why a given run didn't end up in memory.
    """

    memorise: bool
    reason: str
    code: MemorizeSkipReason = MemorizeSkipReason.OK


def should_memorize(
    *,
    status: str,
    memory_scope: str | None,
    eligible_statuses: Iterable[str] | None = None,
) -> MemorizeDecision:
    """Return a :class:`MemorizeDecision` for one execution.

    Args:
        status: The terminal status of the execution
            (`ExecutionStatus` value). Only an eligible status triggers
            memorisation (default ``{"done"}``).
        memory_scope: The agent's `memory_scope` column. NULL or any
            value outside the four canonical `MemoryScope` values
            opts the agent out.
        eligible_statuses: Operator-configurable set of statuses that warrant
            memorisation (Plan 06.17 task_06_17_04, read from
            ``memory.memorizable_statuses``). ``None`` falls back to the default
            ``{"done"}`` so existing callers/tests keep the historical behaviour.

    Examples
    --------
    >>> should_memorize(status="done", memory_scope="team_shared").memorise
    True
    >>> should_memorize(status="aborted", memory_scope="team_shared").memorise
    False
    >>> should_memorize(status="done", memory_scope="none").memorise
    False
    >>> should_memorize(status="done", memory_scope=None).memorise
    False
    """
    eligible = (
        frozenset(eligible_statuses)
        if eligible_statuses is not None
        else _DEFAULT_ELIGIBLE_STATUSES
    )
    if status not in eligible:
        return MemorizeDecision(
            memorise=False,
            reason=f"execution.status={status!r} is not eligible (allowed: {sorted(eligible)})",
            code=MemorizeSkipReason.NOT_DONE,
        )
    if memory_scope is None:
        return MemorizeDecision(
            memorise=False,
            reason="agent.memory_scope is NULL",
            code=MemorizeSkipReason.NO_SCOPE,
        )
    if memory_scope not in _MEMORISABLE_SCOPES:
        return MemorizeDecision(
            memorise=False,
            reason=f"agent.memory_scope={memory_scope!r} is not a canonical MemoryScope",
            code=MemorizeSkipReason.NO_SCOPE,
        )
    return MemorizeDecision(memorise=True, reason="ok", code=MemorizeSkipReason.OK)


def should_memorize_human_session(
    *, task_status: str, memory_scope: str | None
) -> MemorizeDecision:
    """Return a :class:`MemorizeDecision` for one finished human work session.

    The human equivalent of :func:`should_memorize` (Plan 16 task_16_15). A
    :class:`~api_server.db.domain.HumanWorkSession` has no ``status`` of its
    own — what matters is whether the TASK it belongs to reached ``done`` (the
    human's deliverable was accepted, whether via ``auto_approve`` or a peer
    reviewer's approval). A task still ``in_review`` / ``blocked`` / re-opened
    is not a positive example to remember yet.

    Args:
        task_status: The :class:`~api_server.db.domain.TaskStatus` value of the
            task the work session belongs to. Only ``done`` triggers
            memorisation.
        memory_scope: The human Agent's ``memory_scope`` column. NULL or any
            value outside the four canonical :class:`MemoryScope` values opts
            the human agent out (same rule as AI agents).

    Examples
    --------
    >>> should_memorize_human_session(task_status="done", memory_scope="private").memorise
    True
    >>> should_memorize_human_session(task_status="in_review", memory_scope="private").memorise
    False
    >>> should_memorize_human_session(task_status="done", memory_scope=None).memorise
    False
    """
    if task_status != "done":
        return MemorizeDecision(
            memorise=False,
            reason=f"task.status={task_status!r} is not 'done'",
        )
    if memory_scope is None:
        return MemorizeDecision(
            memorise=False,
            reason="agent.memory_scope is NULL",
        )
    if memory_scope not in _MEMORISABLE_SCOPES:
        return MemorizeDecision(
            memorise=False,
            reason=f"agent.memory_scope={memory_scope!r} is not a canonical MemoryScope",
        )
    return MemorizeDecision(memorise=True, reason="ok")


# ---------------------------------------------------------------------------
# ADR 0071 — gobernanza de scope por equipo + enrutado por tipo
# ---------------------------------------------------------------------------

# Orden de amplitud de los scopes (de más estrecho a más amplio). Acota el
# episodic a project_shared sin pasarse del scope efectivo.
_SCOPE_RANK: dict[str, int] = {
    MemoryScope.PRIVATE.value: 0,
    MemoryScope.PROJECT_SHARED.value: 1,
    MemoryScope.TEAM_SHARED.value: 2,
    MemoryScope.GLOBAL.value: 3,
}


def resolve_effective_memory_scope(
    team_memory_scope: str | None,
    agent_memory_scope: str | None,
    platform_default_scope: str,
) -> str:
    """Scope efectivo de una ejecución (ADR 0071): manda el **equipo** del
    contexto (``project.team_id``); si el equipo no fija política, el del
    **agente**; si tampoco, el **default de plataforma**. El primero no vacío
    gana."""
    return team_memory_scope or agent_memory_scope or platform_default_scope


def route_scope_for_type(effective_scope: str, mem_type: str | None) -> str:
    """Enruta una memoria según su tipo (ADR 0071): ``semantic`` (lección/regla)
    viaja al ``effective_scope``; ``episodic`` — y cualquier tipo desconocido,
    por prudencia — se acota a ``project_shared`` (nunca más amplio que el
    efectivo). Así la pericia se comparte y lo puntual queda en su proyecto."""
    if mem_type == "semantic":
        return effective_scope
    project_shared = MemoryScope.PROJECT_SHARED.value
    if _SCOPE_RANK.get(effective_scope, 0) > _SCOPE_RANK[project_shared]:
        return project_shared
    return effective_scope


__all__ = [
    "MemorizeDecision",
    "MemorizeSkipReason",
    "resolve_effective_memory_scope",
    "route_scope_for_type",
    "should_memorize",
    "should_memorize_human_session",
]
