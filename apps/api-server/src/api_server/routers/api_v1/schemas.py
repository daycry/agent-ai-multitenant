"""Public v1 request schemas (Plan 13 task_13_05, Fase B).

The v1 surface RESPONDS with the same public schemas the interactive
routers already expose (``ProjectResponse``, ``PlanResponse``, ...), so
there is no second response shape to keep in sync. It only needs its own
REQUEST bodies: deliberately SLIM compared to the interactive create
requests so the public contract does not expose internal-only knobs
(budget envelopes, MCP/RAG config blobs, approval policies, agent
assignment, dependency wiring). External tooling creates a minimal entity;
the rich configuration stays an interactive-UI / Tenant-Admin concern.

All fields are validated (length bounds, enum membership) so a malformed
body is a clean 422.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.conversation import ChatMode
from api_server.db.domain import PlanStatus, ProjectStatus, TaskPriority, TaskStatus

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class V1ProjectCreateRequest(BaseModel):
    """Minimal public body to create a project."""

    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE


class V1PlanCreateRequest(BaseModel):
    """Minimal public body to create a (draft) plan in a project.

    The specification is left empty; the public API creates the plan
    shell — filling the canonical-template spec stays a planning-chat /
    interactive concern.
    """

    model_config = _BASE_CONFIG

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: PlanStatus = PlanStatus.DRAFT


class V1TaskCreateRequest(BaseModel):
    """Minimal public body to create a task in a project."""

    model_config = _BASE_CONFIG

    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus = TaskStatus.BACKLOG
    priority: TaskPriority = TaskPriority.MEDIUM


class V1ConversationCreateRequest(BaseModel):
    """Minimal public body to start a conversation in a project."""

    model_config = _BASE_CONFIG

    title: str | None = Field(default=None, max_length=255)
    current_mode: ChatMode = ChatMode.PLANNING


class V1KnowledgeBaseCreateRequest(BaseModel):
    """Minimal public body to create a knowledge base."""

    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    embedding_model_id: str | None = Field(default=None, max_length=120)


__all__ = [
    "V1ConversationCreateRequest",
    "V1KnowledgeBaseCreateRequest",
    "V1PlanCreateRequest",
    "V1ProjectCreateRequest",
    "V1TaskCreateRequest",
]
