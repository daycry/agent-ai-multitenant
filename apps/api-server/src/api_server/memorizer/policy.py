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

from dataclasses import dataclass

from api_server.db.domain import ExecutionStatus, MemoryScope

# The four canonical scopes that mean "yes, persist memories".
_MEMORISABLE_SCOPES: frozenset[str] = frozenset(s.value for s in MemoryScope)


@dataclass(frozen=True)
class MemorizeDecision:
    """Outcome of `should_memorize`.

    ``memorise`` is what the caller branches on. ``reason`` is the
    string we log when ``memorise`` is False, so the operator can tell
    why a given execution didn't end up in the memory store.
    """

    memorise: bool
    reason: str


def should_memorize(*, status: str, memory_scope: str | None) -> MemorizeDecision:
    """Return a :class:`MemorizeDecision` for one execution.

    Args:
        status: The terminal status of the execution
            (`ExecutionStatus` value). Only ``done`` triggers
            memorisation.
        memory_scope: The agent's `memory_scope` column. NULL or any
            value outside the four canonical `MemoryScope` values
            opts the agent out.

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
    if status != ExecutionStatus.DONE.value:
        return MemorizeDecision(
            memorise=False,
            reason=f"execution.status={status!r} is not 'done'",
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


__all__ = ["MemorizeDecision", "should_memorize"]
