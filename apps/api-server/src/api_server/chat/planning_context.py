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
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.conversation import Conversation, Message
from api_server.db.conversation_compression import load_context_window
from api_server.db.domain import Plan, Project, Task

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
        }


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

    return PlanningContext(
        project_id=str(project.id),
        project_name=project.name,
        chat_messages=chat_messages,
        kanban=kanban,
        prior_plans=prior_plans,
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


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "KanbanSummary",
    "PlanningContext",
    "PriorPlanSummary",
    "build_planning_context",
]
