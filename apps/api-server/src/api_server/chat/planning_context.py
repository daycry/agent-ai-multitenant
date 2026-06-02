"""Planning context builder (Plan 03 task_03_10).

The multi-agent planning sub-graph (task_03_09) needs more than the
chat history to make good decisions. This module assembles the full
context payload from the database:

  - The conversation's recent messages (compressed via task_03_04).
  - The project's current Kanban — non-terminal tasks the team is
    working on, so the PM doesn't propose duplicate work.
  - The project's prior plans (titles + statuses) so the team can
    refer back to previous decisions.
  - Memory + KB placeholders that Plan 04 will fill in. We expose
    them as empty lists now so the sub-graph contract is stable.
  - Project config the team should respect: approval policy summary,
    team composition, repository config presence.

The returned dict is what the chat endpoint passes as
``project_context`` into `run_planning_turn`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.cost import HumanAgentEstimateInput
from api_server.db.conversation import Conversation, Message
from api_server.db.conversation_compression import load_context_window
from api_server.db.domain import (
    Agent,
    AgentScope,
    AgentType,
    HumanAgentConfig,
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    Plan,
    Project,
    Task,
)

# Statuses we consider "still in the team's hands" for the kanban
# summary. Terminal statuses (done / cancelled) are filtered out so the
# PM focuses on outstanding work.
ACTIVE_TASK_STATUSES: frozenset[str] = frozenset(
    {
        "backlog",
        "ready",
        "in_progress",
        "in_review",
        "blocked",
        "awaiting_human_approval",
    }
)


@dataclass(frozen=True)
class KanbanSummary:
    total: int
    by_status: dict[str, int] = field(default_factory=dict)
    titles_by_status: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class PriorPlanSummary:
    id: str
    title: str
    status: str


# A Human Agent with this many or more live (pending_acceptance / accepted)
# assignments is flagged ``overloaded`` so the planning chat can warn before
# the PM puts another task on its critical path (Plan 16 task_16_13 optional
# alert). Cheap: it rides the same workload count we already load per agent.
HUMAN_AGENT_OVERLOAD_THRESHOLD = 3


@dataclass(frozen=True)
class HumanAgentOption:
    """One assignable Human Agent the PM sees in the planning gallery.

    Folds the tenant's :class:`~api_server.db.domain.Agent`
    (``agent_type='human'``) with its 1:1
    :class:`~api_server.db.domain.HumanAgentConfig` planning inputs (rate +
    expected times) AND a live workload count (open ``pending_acceptance`` /
    ``accepted`` assignments). The PM can assign a plan task to one of these
    exactly like an AI agent; the estimate then sizes the task from these
    figures (:func:`~api_server.chat.cost.compute_human_agent_plan_estimate`).
    """

    agent_id: str
    name: str
    role: str
    assigned_user_id: str | None
    hourly_rate: Decimal | None
    currency: str
    expected_response_time_hours: int | None
    expected_execution_time_hours: int | None
    #: Open assignments (pending_acceptance + accepted) currently on this agent.
    active_assignment_count: int
    #: True when ``active_assignment_count`` >= HUMAN_AGENT_OVERLOAD_THRESHOLD.
    overloaded: bool

    def as_estimate_input(self) -> HumanAgentEstimateInput:
        """The pure-function estimate input this option carries (task_16_13)."""
        return HumanAgentEstimateInput(
            agent_id=self.agent_id,
            name=self.name,
            hourly_rate=self.hourly_rate,
            currency=self.currency,
            expected_response_time_hours=self.expected_response_time_hours,
            expected_execution_time_hours=self.expected_execution_time_hours,
        )


@dataclass(frozen=True)
class PlanningContext:
    """The full payload the sub-graph reads.

    Each field is independently testable and frozen so the graph
    cannot mutate it by accident across turns.
    """

    project_id: str
    project_name: str
    chat_messages: tuple[dict[str, Any], ...]
    kanban: KanbanSummary
    prior_plans: tuple[PriorPlanSummary, ...]
    # The tenant's assignable Human Agents (the gallery) — the PM picks from
    # these to assign a plan task to a human exactly like to an AI agent
    # (Plan 16 task_16_13). Empty when the tenant has no Human Agents.
    human_agents: tuple[HumanAgentOption, ...] = ()
    # Plan 04 fills these in. Kept here so the sub-graph contract is
    # already shaped for them and the planning prompts can reference
    # the fields when they exist.
    memory_snippets: tuple[dict[str, Any], ...] = ()
    kb_documents: tuple[dict[str, Any], ...] = ()
    # Project-side knobs that influence planning. Surfaced as bools /
    # short summaries instead of the raw JSONB so the prompt stays
    # readable.
    has_approval_policy: bool = False
    has_repository_config: bool = False
    team_id: str | None = None

    def as_graph_payload(self) -> dict[str, Any]:
        """Render to the dict shape `PlanningState.project_context`
        expects. Used by the chat endpoint when bridging into the graph."""
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "kanban_total": self.kanban.total,
            "kanban_by_status": dict(self.kanban.by_status),
            "kanban_titles_by_status": {
                k: list(v) for k, v in self.kanban.titles_by_status.items()
            },
            "prior_plans": [
                {"id": p.id, "title": p.title, "status": p.status} for p in self.prior_plans
            ],
            "memory_snippets": list(self.memory_snippets),
            "kb_documents": list(self.kb_documents),
            "has_approval_policy": self.has_approval_policy,
            "has_repository_config": self.has_repository_config,
            "team_id": self.team_id,
            "human_agents": [
                {
                    "agent_id": h.agent_id,
                    "name": h.name,
                    "role": h.role,
                    "assigned_user_id": h.assigned_user_id,
                    "hourly_rate": str(h.hourly_rate) if h.hourly_rate is not None else None,
                    "currency": h.currency,
                    "expected_response_time_hours": h.expected_response_time_hours,
                    "expected_execution_time_hours": h.expected_execution_time_hours,
                    "active_assignment_count": h.active_assignment_count,
                    "overloaded": h.overloaded,
                }
                for h in self.human_agents
            ],
        }

    def human_agent_estimate_inputs(self) -> dict[str, HumanAgentEstimateInput]:
        """The ``{agent_id: HumanAgentEstimateInput}`` map the cost layer wants.

        Handed straight to
        :func:`~api_server.chat.cost.compute_human_agent_plan_estimate` so the
        plan estimate sizes each human-agent-assigned task from the agent's
        configured rate + expected times (Plan 16 task_16_13).
        """
        return {h.agent_id: h.as_estimate_input() for h in self.human_agents}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
async def build_planning_context(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    chat_window_size: int = 50,
    kanban_titles_per_status: int = 10,
    prior_plans_limit: int = 20,
) -> PlanningContext:
    """Assemble the full PlanningContext for a conversation.

    Raises:
        ValueError: when ``conversation_id`` does not resolve under the
            session's RLS scope. The caller has already validated the
            id, so this is a programmer error.
    """
    conversation = await _load_conversation_or_raise(session, conversation_id)
    project = await _load_project_or_raise(session, conversation.project_id)

    chat_messages = await _load_chat_window(session, conversation_id, max_messages=chat_window_size)
    kanban = await _build_kanban_summary(
        session, conversation.project_id, titles_cap=kanban_titles_per_status
    )
    prior_plans = await _load_prior_plans(session, conversation.project_id, limit=prior_plans_limit)
    human_agents = await _load_human_agents(session)

    return PlanningContext(
        project_id=str(project.id),
        project_name=project.name,
        chat_messages=chat_messages,
        kanban=kanban,
        prior_plans=prior_plans,
        human_agents=human_agents,
        memory_snippets=(),
        kb_documents=(),
        has_approval_policy=project.human_approval_policy is not None,
        has_repository_config=project.repository_config is not None,
        team_id=str(project.team_id) if project.team_id else None,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
async def _load_conversation_or_raise(session: AsyncSession, conversation_id: UUID) -> Conversation:
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.deleted_at is not None:
        raise ValueError(f"conversation {conversation_id} not visible or deleted")
    return conv


async def _load_project_or_raise(session: AsyncSession, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise ValueError(f"project {project_id} not visible or deleted")
    return project


async def _load_chat_window(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    max_messages: int,
) -> tuple[dict[str, Any], ...]:
    """Use the compression-aware loader so summaries stand in for
    older history."""
    messages: list[Message] = await load_context_window(
        session, conversation_id, max_messages=max_messages
    )
    return tuple(
        {
            "id": str(m.id),
            "author_kind": m.author_kind,
            "content": m.content,
            "mode": m.mode,
            "is_summary": m.is_summary,
        }
        for m in messages
    )


async def _build_kanban_summary(
    session: AsyncSession,
    project_id: UUID,
    *,
    titles_cap: int,
) -> KanbanSummary:
    result = await session.execute(
        select(Task.status, Task.title).where(
            Task.project_id == project_id,
            Task.status.in_(ACTIVE_TASK_STATUSES),
        )
    )
    rows = list(result.all())
    by_status: dict[str, int] = {}
    titles_by_status: dict[str, list[str]] = {}
    for status, title in rows:
        by_status[status] = by_status.get(status, 0) + 1
        bucket = titles_by_status.setdefault(status, [])
        if len(bucket) < titles_cap:
            bucket.append(title)
    return KanbanSummary(
        total=len(rows),
        by_status=by_status,
        titles_by_status=titles_by_status,
    )


async def _load_prior_plans(
    session: AsyncSession,
    project_id: UUID,
    *,
    limit: int,
) -> tuple[PriorPlanSummary, ...]:
    result = await session.execute(
        select(Plan.id, Plan.title, Plan.status)
        .where(Plan.project_id == project_id, Plan.deleted_at.is_(None))
        .order_by(Plan.created_at.desc())
        .limit(limit)
    )
    return tuple(
        PriorPlanSummary(id=str(pid), title=title, status=status)
        for pid, title, status in result.all()
    )


# Open assignment states that count toward a Human Agent's live workload — the
# task is on the human's plate but not yet finished (pending_acceptance =
# awaiting accept; accepted = working). reassigned/declined/expired are closed.
_OPEN_ASSIGNMENT_STATES: frozenset[str] = frozenset(
    {
        HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value,
        HumanTaskAssignmentStatus.ACCEPTED.value,
    }
)


async def _load_human_agents(session: AsyncSession) -> tuple[HumanAgentOption, ...]:
    """Load the tenant's assignable Human Agents (the planning gallery).

    The PM picks from these to assign a plan task to a human exactly like to an
    AI agent (Plan 16 task_16_13). Returns the tenant's own Human Agents (NOT
    the global ``global_builtin`` templates — those carry no config and cannot
    be assigned until forked, mirroring ``GET /human-agents``), each folded with
    its :class:`HumanAgentConfig` planning inputs and a live workload count.

    RLS scopes every row to the caller's tenant; the workload count is a
    correlated sub-select of OPEN (pending_acceptance / accepted) assignments on
    the same agent, so it too is tenant-scoped. The optional overload flag
    (task_16_13) is derived from that count — cheap, no extra round-trip.
    """
    workload_sq = (
        select(func.count())
        .select_from(HumanTaskAssignment)
        .where(
            HumanTaskAssignment.human_agent_id == Agent.id,
            HumanTaskAssignment.status.in_(_OPEN_ASSIGNMENT_STATES),
        )
        .correlate(Agent)
        .scalar_subquery()
    )
    stmt = (
        select(Agent, HumanAgentConfig, workload_sq.label("active_assignments"))
        .join(HumanAgentConfig, HumanAgentConfig.agent_id == Agent.id)
        .where(
            Agent.agent_type == AgentType.HUMAN.value,
            Agent.scope != AgentScope.GLOBAL_BUILTIN.value,
            Agent.deleted_at.is_(None),
        )
        .order_by(Agent.name, Agent.id)
    )
    rows = (await session.execute(stmt)).all()
    options: list[HumanAgentOption] = []
    for agent, config, active in rows:
        count = int(active or 0)
        options.append(
            HumanAgentOption(
                agent_id=str(agent.id),
                name=agent.name,
                role=agent.role,
                assigned_user_id=(
                    str(config.assigned_user_id) if config.assigned_user_id is not None else None
                ),
                hourly_rate=config.hourly_rate,
                currency=config.hourly_rate_currency or "EUR",
                expected_response_time_hours=config.expected_response_time_hours,
                expected_execution_time_hours=config.expected_execution_time_hours,
                active_assignment_count=count,
                overloaded=count >= HUMAN_AGENT_OVERLOAD_THRESHOLD,
            )
        )
    return tuple(options)


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "HUMAN_AGENT_OVERLOAD_THRESHOLD",
    "HumanAgentOption",
    "KanbanSummary",
    "PlanningContext",
    "PriorPlanSummary",
    "build_planning_context",
]
