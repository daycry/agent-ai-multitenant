"""Per-agent tool enforcement (Plan 06.15 task_06_15_02).

Plan 06.15 task_06_15_01 added the write surface (``PUT /agents/{id}/tools``)
that fills the ``agent_tools`` M:N junction. This module is the *read /
resolve* seam that turns those rows into the allowlist the agent-runtime's
``ToolRegistry`` enforces at call time.

Two pure pieces, kept free of router/HTTP concerns so the orchestrator
(which builds the worker run payload) and the integration tests can both
import them:

  * :func:`resolve_agent_tool_names` — the async DB read: the set of
    ``Tool.name`` values wired to one agent, or ``None`` when the agent has
    no rows. The ``None`` sentinel is load-bearing: **no rows means no
    per-agent restriction**, the backward-compatible behaviour for every
    agent that existed before this plan. An agent that *does* have rows is
    restricted to exactly those tool names.

  * :func:`combine_tool_allowlists` — the pure combination of the per-agent
    set with the active chat mode's allowlist (``ChatModeConfig.allowed_tools``,
    task_06_14_07). Both are independent restrictions, so the effective set
    is their **intersection**; either being ``None`` means "that layer does
    not restrict". The result is what gets threaded into the task spec's
    ``allowed_tools`` (``ExecutionRequest.allowed_tools`` →
    ``_agent_spec`` → ``AGENT_TASK_SPEC`` → ``ToolRegistry.set_allowed_tools``).

The runtime already rejects a tool outside its configured allowlist before
the tool function runs (see ``agent_runtime.tools.ToolRegistry.call``); this
module only decides *what* that allowlist is for a given agent + mode. It is
NOT the layered guardrail engine (Plan 11) — it is the minimal call-time
enforcement that makes a per-agent assignment real instead of advisory.

Tool *names* (not ids) are forwarded: the runtime registry is keyed on the
canonical ``Tool.name`` (e.g. ``read_file``), which is also what the chat-mode
allowlists are expressed in — so the intersection is over a single namespace.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import AgentTool, Tool


async def resolve_agent_tool_names(session: AsyncSession, agent_id: UUID) -> frozenset[str] | None:
    """The set of ``Tool.name`` values assigned to ``agent_id``, or ``None``.

    Returns ``None`` when the agent has **no** ``agent_tools`` rows — the
    signal for "no per-agent restriction" (current behaviour, no regression).
    A non-empty frozenset restricts the agent to exactly those tool names.

    Soft-deleted tools are excluded; an assignment whose tool was deleted
    simply does not contribute a name (it never becomes callable anyway).

    The query is tenant-safe by construction: under a tenant-scoped session
    RLS hides cross-tenant agents/tools, and the orchestrator (BYPASSRLS)
    only ever calls this for an agent it has already resolved within the
    task's tenant.
    """
    rows = await session.execute(
        select(Tool.name)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent_id, Tool.deleted_at.is_(None))
    )
    names = rows.scalars().all()
    if not names:
        return None
    return frozenset(names)


def combine_tool_allowlists(
    agent_tool_names: Iterable[str] | None,
    mode_allowed_tools: Iterable[str] | None,
) -> list[str] | None:
    """Intersect the per-agent assignment with the chat-mode allowlist.

    Each argument is an independent restriction layer:

      * ``agent_tool_names`` — the agent's ``agent_tools`` set, or ``None``
        when the agent has no assignments (no per-agent restriction).
      * ``mode_allowed_tools`` — ``ChatModeConfig.allowed_tools`` for the
        active mode, or ``None`` when the run carries no mode allowlist
        (e.g. the orchestrator's task-dispatch path).

    Semantics:

      * both ``None`` → ``None`` (unrestricted — backward compatible).
      * exactly one set → that set (the only active restriction).
      * both set → their intersection (a tool must satisfy *both* layers).

    The result is a sorted ``list[str]`` (deterministic for the JSON spec /
    tests) or ``None``. An empty list IS a valid result — it means "the two
    layers share no tool", which the runtime reads as "block every tool",
    exactly as the discussion mode's empty allowlist already does.
    """
    agent_set = None if agent_tool_names is None else frozenset(agent_tool_names)
    mode_set = None if mode_allowed_tools is None else frozenset(mode_allowed_tools)

    if agent_set is None and mode_set is None:
        return None
    if agent_set is None:
        return sorted(mode_set or frozenset())
    if mode_set is None:
        return sorted(agent_set)
    return sorted(agent_set & mode_set)


__all__ = ["combine_tool_allowlists", "resolve_agent_tool_names"]
