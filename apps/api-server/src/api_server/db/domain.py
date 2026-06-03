"""Phase 1 domain models — agents, skills, tools, teams, projects, plans, tasks.

Eleven entities derived from spec §3.1 and §4.2:

  Agent              reusable template; AI or human, with prompt + tools.
  Skill              declarative capability (prompt fragment, no exec code).
  Tool               executable function; type/security_level/schemas.
  AgentSkill         M:N junction with proficiency.
  AgentTool          M:N junction with per-agent config_override.
  Team               group of agents with optional default workflow.
  TeamMember         M:N junction with role-in-team + leader flag.
  Project            work container; budget, repository, approval policy.
  Plan               materialized output of a planning session.
  Task               unit of work; flows through the Kanban statuses.
  TaskDependency     M:N self-join expressing "depends-on".

Notes:
  - tenant_id (via TenantScopedMixin) is the multi-tenancy boundary;
    RLS policies on every table enforce isolation.
  - agent_type, scope and the linked-vs-forked fields arrive in task_01_03;
    this file ships the base shape per spec §4.2.1.
  - JSON-shaped columns (model_config, mcp_servers, ...) use JSONB so
    we can index into them later without migrations.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Plan 03: Conversation/Message live in their own module but are re-exported
# from `domain` so existing `from api_server.db import domain as d` callers
# keep finding the whole multi-table domain in one place.
from api_server.db.conversation import (  # (re-export)
    ChatMode,
    Conversation,
    Message,
    MessageAuthorKind,
)


# =============================================================================
# Enums (StrEnum so values are stable strings persisted as TEXT)
# =============================================================================
class AgentType(enum.StrEnum):
    AI = "ai"
    HUMAN = "human"


class AssignmentMode(enum.StrEnum):
    """How a human agent's tasks are routed to actual people (Plan 16).

    - ``SPECIFIC_USER``: tasks go to one fixed :class:`User`
      (``human_agent_config.assigned_user_id``). The ONLY mode in the MVP
      (Plan 16 Decisiones Clave) — a DB CHECK constrains the column to it.
    - ``ROLE_QUEUE`` / ``TEAM_POOL``: queue- and team-based routing. Modelled
      here for forward-compatibility but explicitly out of scope this plan;
      the CHECK rejects them until a later plan lifts the constraint.
    """

    SPECIFIC_USER = "specific_user"
    ROLE_QUEUE = "role_queue"
    TEAM_POOL = "team_pool"


class AgentScope(enum.StrEnum):
    """Where an agent lives in the linked-vs-forked taxonomy (spec §5.7.5).

    - GLOBAL_BUILTIN: shipped with the platform, owned by the platform
      tenant. Visible to every tenant; only system admins write.
    - GLOBAL_TENANT_TEMPLATE: a tenant's own template, available to its
      projects.
    - PROJECT_LOCAL: tied to a single project; `project_id` is mandatory.
    """

    GLOBAL_BUILTIN = "global_builtin"
    GLOBAL_TENANT_TEMPLATE = "global_tenant_template"
    PROJECT_LOCAL = "project_local"


class AgentRole(enum.StrEnum):
    """Built-in agent roles. CUSTOM is the escape hatch for tenant-defined
    roles whose label lives in `description` or a future role catalog."""

    PROJECT_MANAGER = "project_manager"
    ARCHITECT = "architect"
    BACKEND_DEV = "backend_dev"
    FRONTEND_DEV = "frontend_dev"
    QA = "qa"
    REVIEWER = "reviewer"
    LEADER = "leader"
    WORKER = "worker"
    SPECIALIST = "specialist"
    RESEARCHER = "researcher"
    DEVOPS = "devops"
    SECURITY = "security"
    TECHNICAL_WRITER = "technical_writer"
    CUSTOM = "custom"


class MemoryScope(enum.StrEnum):
    PRIVATE = "private"
    TEAM_SHARED = "team_shared"
    PROJECT_SHARED = "project_shared"
    GLOBAL = "global"


class MemoryType(enum.StrEnum):
    """Episodic vs semantic distinction for agent memory (Plan 04 task_04_01).

    - ``episodic``: a concrete event the agent lived through ("the
      psycopg3 import failed in project X on 2026-05-25").
    - ``semantic``: a rule or generalisation distilled from one or
      more episodes ("project X uses asyncpg, not psycopg3").

    The Memorizer (`task_04_03`) decides which type each entry gets
    when it distils an `Execution`; the `memory_recall` tool can
    filter by type."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class SkillCategory(enum.StrEnum):
    """Open-ended skill grouping. Free-form `category` text is also accepted;
    this enum is just for the curated catalog (see task_01_10 seed)."""

    CODING = "coding"
    REVIEW = "review"
    PLANNING = "planning"
    RESEARCH = "research"
    DEVOPS = "devops"
    DATA = "data"
    DOCS = "docs"
    QA = "qa"
    SECURITY = "security"


class ToolCategory(enum.StrEnum):
    """Closed *Función* facet of the tool taxonomy (ADR 0049).

    The seven function buckets are exactly the ``category`` values used by the
    19 built-in seed rows (``api_server.seeds.builtin_tools``); a CI contract
    test (task_06_18_14) asserts this enum stays a superset of the seed.

    Three extra buckets carry tools that are *not* in the catalog seed:

      - ``MCP`` — tools imported from an MCP server (origin facet ``mcp_tool``;
        ADR 0052), namespaced ``<server>.<tool>``.
      - ``ORCHESTRATION`` — runtime-registered orchestration tools
        (``kanban_update`` / ``task_comment`` / ``agent_invoke``; ADR 0048).
      - ``CUSTOM`` — the catch-all for tenant-authored tools that do not map to
        a function bucket above.

    The DB-level CHECK on ``tools.category`` (migration 0077) is built from this
    same value set so the database and the application agree.
    """

    FILE = "file"
    RUNTIME = "runtime"
    GIT = "git"
    NETWORK = "network"
    KNOWLEDGE = "knowledge"
    NOTIFICATION = "notification"
    COMMAND = "command"
    MCP = "mcp"
    ORCHESTRATION = "orchestration"
    CUSTOM = "custom"


class ToolImplementationType(enum.StrEnum):
    BUILTIN = "builtin"
    PYTHON_FUNCTION = "python_function"
    HTTP_ENDPOINT = "http_endpoint"
    MCP_TOOL = "mcp_tool"
    DOCKER_COMMAND = "docker_command"


class ToolSecurityLevel(enum.StrEnum):
    SAFE = "safe"
    SANDBOXED = "sandboxed"
    PRIVILEGED = "privileged"


class AgentSkillProficiency(enum.StrEnum):
    BASIC = "basic"
    STANDARD = "standard"
    EXPERT = "expert"


class ProjectStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class HumanTaskReviewMode(enum.StrEnum):
    """How a human task's deliverable is reviewed once submitted (Plan 16
    task_16_11), a project-level setting (``projects.human_task_review_mode``).

    - ``AUTO_APPROVE`` (default): the submit path (task_16_09) takes the task
      straight to ``done`` — no extra review step. The MVP default (Plan 16
      Decisiones Clave): suitable for "firma"-style tasks where the act of
      submitting IS the completion.
    - ``PEER_HUMAN_REVIEWER``: the task stays ``in_review`` and a SECOND
      :class:`HumanTaskAssignment` is created for another Human Agent (the
      reviewer), who approves (-> ``done``) or rejects with feedback (-> back
      to ``backlog`` with ``retry_count`` bumped; after ``max_retries`` the
      §7.9 retry/escalation infra parks it in ``awaiting_human_approval``).

    The ``ai_reviewer`` mode is explicitly out of scope this plan (Plan 16
    Alcance); the DB CHECK ``ck_projects_human_task_review_mode`` rejects any
    value outside these two.
    """

    AUTO_APPROVE = "auto_approve"
    PEER_HUMAN_REVIEWER = "peer_human_reviewer"


class BudgetPeriod(enum.StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class PlanStatus(enum.StrEnum):
    """Full lifecycle of a plan (Plan 03 task_03_16 / task_03_25).

    Transitions are enforced in `api_server.chat.plan_state_machine`.
    A freshly POSTed plan from the chat lands in ``draft``; the human
    moves it to ``pending_approval`` to start the review.

    When the AI cost estimate exceeds the platform-configured double-
    signature threshold (task_03_25), the first approval moves the
    plan to ``pending_second_approval``; a **different** signer must
    confirm to reach ``approved``. Below the threshold a single firma
    is enough (``pending_approval -> approved``).

    Executions of approved plans flip them through ``in_progress``,
    ``blocked``, then ``pending_human_validation`` and finally
    ``completed``.
    """

    PENDING_APPROVAL = "pending_approval"
    PENDING_SECOND_APPROVAL = "pending_second_approval"
    DRAFT = "draft"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PENDING_HUMAN_VALIDATION = "pending_human_validation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class TaskStatus(enum.StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    # La tarea ha sido asignada a un Human Agent (agent_type=human) y
    # espera que el User asignado la acepte (Plan 16 §7.2 / task_16_04).
    # Solo alcanzable desde `ready` y SOLO para tareas cuyo agente asignado
    # es humano — el orchestrator (task_16_05) la fija al rutear una tarea
    # humana en lugar de pedir contenedor. Desde aquí: `in_progress` (el
    # humano acepta), `assigned_to_human` (reasignación) o `blocked`
    # (escalación agotada, task_16_06).
    ASSIGNED_TO_HUMAN = "assigned_to_human"
    IN_PROGRESS = "in_progress"
    # La tarea está aparcada esperando una decisión humana sobre una
    # acción sensible (ADR 0020). El agente queda libre; al aprobar la
    # tarea vuelve a `backlog`; al rechazar pasa a `blocked`.
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskComplexity(enum.StrEnum):
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


class ExecutionStatus(enum.StrEnum):
    RUNNING = "running"
    DONE = "done"
    ABORTED = "aborted"
    FAILED = "failed"
    # Paused mid-run waiting on a human_approval_policy decision (Fase F).
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"


class DocumentStatus(enum.StrEnum):
    """Lifecycle of a `Document` in a Knowledge Base (Plan 04 task_04_07).

    A freshly uploaded document lands in ``pending``. The ingestion
    worker (Plan 04 Fase C, task_04_11) flips it to ``processing``
    while Docling parses + chunks + embeds, then to ``indexed`` on
    success or ``failed`` on error. The transitions are linear and
    one-way — re-ingestion creates a *new* Document row, never
    rewinds the existing one.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class ApprovalRequestStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class HumanTaskAssignmentStatus(enum.StrEnum):
    """Lifecycle of one :class:`HumanTaskAssignment` (Plan 16 task_16_05).

    The assignment row tracks WHO a human task is currently with and where
    that person is in the accept/work cycle — distinct from, but kept in step
    with, the Task's own §7.2 status (an ``assigned_to_human`` Task has a
    ``pending_acceptance`` assignment; the Task moves to ``in_progress`` when
    the assignment moves to ``accepted``).

    - ``PENDING_ACCEPTANCE``: freshly created by the orchestrator (task_16_05)
      — the assigned user has been notified and has up to
      ``acceptance_timeout_hours`` to accept before escalation (task_16_06).
    - ``ACCEPTED``: the user accepted; work has started.
    - ``REASSIGNED``: superseded by a newer assignment (the acceptance-timeout
      escalation hands the task to the ``escalation_target_user_id``,
      task_16_06). The superseding row is a fresh ``pending_acceptance`` one.
    - ``DECLINED``: the user explicitly rejected the task (Fase C inbox).
    - ``EXPIRED``: the acceptance window lapsed with no decision (task_16_06).
    """

    PENDING_ACCEPTANCE = "pending_acceptance"
    ACCEPTED = "accepted"
    REASSIGNED = "reassigned"
    DECLINED = "declined"
    EXPIRED = "expired"


# =============================================================================
# Agent
# =============================================================================
class Agent(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "agents"
    __table_args__ = (
        Index(
            "ix_agents_tenant_role",
            "tenant_id",
            "role",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_agents_scope_project", "scope", "project_id"),
        Index("ix_agents_forked_from", "forked_from_agent_id"),
        # scope <-> project_id invariant (spec §5.7.5):
        #   global_builtin / global_tenant_template -> project_id IS NULL
        #   project_local                           -> project_id IS NOT NULL
        CheckConstraint(
            "(scope = 'project_local' AND project_id IS NOT NULL)"
            " OR (scope IN ('global_builtin', 'global_tenant_template')"
            "     AND project_id IS NULL)",
            name="ck_agents_scope_project_consistency",
        ),
        # agent_type enum value set (Plan 16 task_16_01). The column itself
        # ships with the domain-minimum migration (0002) as String(16) NOT
        # NULL DEFAULT 'ai'; migration 0066 adds this CHECK so the DB enforces
        # the AgentType value set (ai|human) instead of accepting any text.
        CheckConstraint(
            "agent_type IN ('ai', 'human')",
            name="ck_agents_agent_type",
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # AI vs human agent (Plan 16). Values are the :class:`AgentType` StrEnum
    # (ai|human) stored as TEXT — same string-backed-enum convention as
    # `scope`/`AgentScope`. Existing rows default to 'ai' (no behaviour change
    # for AI agents). DB-enforced by ck_agents_agent_type (migration 0066).
    agent_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'ai'"))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    memory_scope: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'private'")
    )
    review_capability: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    max_concurrent_tasks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    is_template: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # --- Linked-vs-forked (spec §5.7.5) ---
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'project_local'")
    )
    # NULL except when scope=project_local. FK is deferred to migration
    # (Project ORM is defined later in this file but ForeignKey resolves
    # by table name at metadata-finalize time).
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Self-FK: if this agent is a fork, points to the origin. ON DELETE
    # SET NULL keeps the local copy alive even if the global vanishes.
    forked_from_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Semver of the global at fork time. Used to compute diffs and to
    # decide whether "merge upstream improvements" is needed.
    forked_from_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Semver the linked is pinned to. NULL = follow the global's 'stable'.
    anchored_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Agent(id={self.id!r}, name={self.name!r}, role={self.role!r})"


# =============================================================================
# Skill
# =============================================================================
class Skill(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "skills"
    __table_args__ = (
        Index(
            "ix_skills_tenant_category",
            "tenant_id",
            "category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_skills_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_fragment: Mapped[str] = mapped_column(Text, nullable=False)
    # List of tool UUIDs. JSONB rather than a junction table -- the
    # association is a *recommendation*, not a hard FK, and tools may
    # come from outside this tenant's catalog (built-ins).
    required_tools: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Catalog marker. true => visible to all tenants via SELECT RLS
    # policy (migration 0005). Writes still go through tenant-isolation
    # (only the platform tenant / BYPASSRLS can create built-ins).
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# Tool
# =============================================================================
class Tool(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tools"
    __table_args__ = (
        Index(
            "ix_tools_tenant_category",
            "tenant_id",
            "category",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_tools_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
        # No two LIVE tools of the same tenant may share a name (a soft-deleted
        # name may be reused). Partial unique index because PostgreSQL UNIQUE
        # constraints cannot carry a WHERE clause (task_06_18_04, ADR 0049).
        Index(
            "uq_tools_tenant_name",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("timeout_seconds > 0", name="ck_tools_timeout_positive"),
        # Closed taxonomy value sets (ADR 0049). The category list mirrors
        # ToolCategory; security_level / implementation_type mirror their enums.
        CheckConstraint(
            "category IN (" + ", ".join(f"'{c.value}'" for c in ToolCategory) + ")",
            name="ck_tools_category",
        ),
        CheckConstraint(
            "security_level IN ('safe', 'sandboxed', 'privileged')",
            name="ck_tools_security_level",
        ),
        CheckConstraint(
            "implementation_type IN ("
            "'builtin', 'python_function', 'http_endpoint', 'mcp_tool', 'docker_command')",
            name="ck_tools_implementation_type",
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)

    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    implementation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    implementation_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    security_level: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'safe'")
    )

    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("60"))
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Catalog marker -- see Skill.is_builtin.
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# Agent-Skill (M:N junction)
# =============================================================================
class AgentSkill(Base, TimestampMixin):
    """Composite PK (agent_id, skill_id). No tenant_id column because both
    parents are tenant-scoped and ON DELETE CASCADE cleans up cross-tenant
    leftovers; RLS is enforced via the parent row visibility."""

    __tablename__ = "agent_skills"
    __table_args__ = (PrimaryKeyConstraint("agent_id", "skill_id", name="pk_agent_skills"),)

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    proficiency: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'standard'")
    )


# =============================================================================
# Agent-Tool (M:N junction)
# =============================================================================
class AgentTool(Base, TimestampMixin):
    __tablename__ = "agent_tools"
    __table_args__ = (PrimaryKeyConstraint("agent_id", "tool_id", name="pk_agent_tools"),)

    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="CASCADE"),
        nullable=False,
    )
    config_override: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# =============================================================================
# Team
# =============================================================================
class Team(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "teams"
    __table_args__ = (
        Index(
            "ix_teams_tenant_name",
            "tenant_id",
            "name",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_teams_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-FK to a future workflow_templates table (Plan 02+). Kept as a
    # nullable UUID without a constraint until that table exists.
    default_workflow_template_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    shared_memory_namespace: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Catalog marker -- same pattern as Skill/Tool.is_builtin.
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# TeamMember (M:N junction)
# =============================================================================
class TeamMember(Base, TimestampMixin):
    __tablename__ = "team_members"
    __table_args__ = (PrimaryKeyConstraint("team_id", "agent_id", name="pk_team_members"),)

    team_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Per-team role label -- can differ from Agent.role (e.g. a backend_dev
    # serving as architect_assistant within a specific team).
    role_in_team: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_team_leader: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    assignment_priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("100")
    )


# =============================================================================
# Project
# =============================================================================
class Project(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "projects"
    __table_args__ = (
        Index(
            "ix_projects_tenant_status",
            "tenant_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_projects_is_template",
            "is_template",
            postgresql_where=text("is_template = true"),
        ),
        CheckConstraint(
            "budget_amount IS NULL OR budget_amount >= 0",
            name="ck_projects_budget_non_negative",
        ),
        # human_task_review_mode value set (Plan 16 task_16_11). Mirrors the
        # HumanTaskReviewMode StrEnum; DB-enforced by migration 0073, same
        # shape as ck_agents_agent_type.
        CheckConstraint(
            "human_task_review_mode IN ('auto_approve', 'peer_human_reviewer')",
            name="ck_projects_human_task_review_mode",
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))

    # Team assignment is optional at creation -- some projects are bootstrapped
    # before a team is decided. Nullable FK; ON DELETE SET NULL keeps the
    # project alive if the team is dissolved.
    team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )

    # JSONB placeholders for things that get their own tables in later
    # plans -- MCP server registry, RAG KBs, repo config, approval policy.
    mcp_servers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    rag_knowledge_bases: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Plan 06.9 task_06_9_07: KBs (by slug) that the wizard should
    # auto-grant when a tenant creates a project from this template.
    # Only meaningful on `is_template=true` rows; ignored otherwise.
    default_kb_grants: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    worker_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    repository_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    human_approval_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Plan 06.16 task_06_16_01: polyglot tool catalog. `allowed_commands`
    # is the per-project deny-by-default allowlist of program *basenames*
    # (`php`, `composer`, `vendor/bin/phpunit`, `pest`, `npm`, …) the
    # `shell_exec` builtin may run; empty `[]` = nothing runs (deny-all).
    # TEXT[] (not JSONB) — membership-only semantics, same shape as
    # `default_kb_grants`. `default_runtime_template` names the stack's
    # runtime template id (`php-phpunit`, `node-jest`, …) the `run_*`
    # tools resolve against; NULL = keep each tool's current default
    # (backward-compatible).
    allowed_commands: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    default_runtime_template: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Soft-FK to the Vault entry that holds the project's secrets. Vault
    # is an external system so no DB-level FK.
    secrets_vault_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # --- Budget (see spec §28.7 for tenant-vs-project budget interaction) ---
    # Numeric(14,2) maps to Decimal in Python so the schema's Decimal
    # type stays consistent end-to-end (no float rounding on currency).
    budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    budget_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    budget_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    budget_period_start_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_period_length_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paused_by_budget: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # --- Human-task review mode (Plan 16 task_16_11) ----------------------
    # How a human task's deliverable is reviewed once submitted (task_16_09).
    # Stored as the :class:`HumanTaskReviewMode` value (TEXT) — same
    # string-backed-enum convention as `status`. DEFAULT 'auto_approve' so
    # existing projects keep the MVP behaviour (submit -> in_review -> done,
    # no extra review step). DB-constrained by ck_projects_human_task_review_mode.
    human_task_review_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'auto_approve'")
    )

    # --- Budget: fold human cost in? (Plan 16 task_16_12) -----------------
    # Human cost (rate * hours from human_work_sessions) is ALWAYS imputed to
    # the plan/project + segmented in the 13.7 dashboard. This flag decides
    # whether it ALSO counts toward this project's BUDGET (consumption +
    # threshold alerts + auto-pause). DEFAULT false = current behaviour (only
    # the canonical-USD AI cost counts); true folds the project's human cost
    # (converted to USD) into the consumption the evaluator compares vs the
    # cap. DB column added by migration 0074.
    budget_includes_human_cost: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    # Catalog marker -- when true the row is a template blueprint owned
    # by the platform tenant, visible cross-tenant via RLS but never the
    # target of writes from a tenant session.
    is_template: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# Plan
# =============================================================================
class Plan(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "plans"
    __table_args__ = (
        Index(
            "ix_plans_tenant_project_status",
            "tenant_id",
            "project_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 32 chars so the wide ten-state machine (pending_approval,
    # pending_human_validation, ...) introduced in task_03_16 fits.
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'draft'"))

    # Was a soft-FK in the Plan 01 migration; promoted to a real FK in
    # migration 0014 once the conversations table existed.
    conversation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # The canonical-template specification (Plan 03 §8.5). JSONB so the
    # shape can evolve without migrations:
    #   {
    #     "summary":      {...},
    #     "phases":       [{name, description, tasks: [{...}]}],
    #     "tasks":        [task_spec],   # flat, dependencies by task_id
    #     "estimates":    {...},
    #     "tests_humans": [{...}],
    #     "metadata":     {...}          # template version, generator, etc.
    #   }
    # Empty `{}` for a freshly created draft until the team fills it in.
    specification: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # First-signature trail for the double-firma flow (task_03_25).
    # NULL on single-signature plans. The state machine asserts the
    # second signer is a different user than `first_approved_by`.
    first_approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    first_approved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


# =============================================================================
# Task
# =============================================================================
class Task(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Tasks are NOT soft-deleted -- they're terminal (done/cancelled) instead.
    Add SoftDeleteMixin in a later phase if the product requires it."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_tenant_status", "tenant_id", "status"),
        Index("ix_tasks_project_plan", "project_id", "plan_id"),
        CheckConstraint("retry_count >= 0", name="ck_tasks_retry_count_non_negative"),
        CheckConstraint("max_retries >= 0", name="ck_tasks_max_retries_non_negative"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="SET NULL"),
        nullable=True,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 32 chars — wide enough for `awaiting_human_approval` (ADR 0020).
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'backlog'")
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'medium'")
    )

    assigned_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewer_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    acceptance_criteria: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    estimated_complexity: Mapped[str | None] = mapped_column(String(4), nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))

    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


# =============================================================================
# TaskDependency (self-M:N: task depends on tasks)
# =============================================================================
class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        PrimaryKeyConstraint("task_id", "depends_on_task_id", name="pk_task_dependencies"),
        # A task can't depend on itself. Cycles among different tasks are
        # caught at application level (DAG check before plan execution).
        CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_task_dependencies_no_self_loop",
        ),
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
    )

    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    depends_on_task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )


# =============================================================================
# ApprovalPolicyTemplate (catalog of named human-approval policies)
# =============================================================================
class ApprovalPolicyTemplate(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """Named bundle of per-category approval decisions (spec §7.7-7.8).

    `categories` shape::

        {"categories": {"code_changes": "auto", "git_push": "human_required", ...}}

    Stored as JSONB so adding a new category later doesn't require a
    migration. The policy *referenced* by a project lives copy-pasted
    in `projects.human_approval_policy` -- this table is just the
    catalog of pickable presets.
    """

    __tablename__ = "approval_policy_templates"
    __table_args__ = (
        Index(
            "ix_approval_policy_templates_is_builtin",
            "is_builtin",
            postgresql_where=text("is_builtin = true"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    categories: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_builtin: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


# =============================================================================
# Execution (one run of the agent loop against a task)
# =============================================================================
class Execution(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One run of the agent loop against a task (spec §13).

    The `steps_log` JSONB column is the heart of the table: an
    append-only array of step records — one per graph node, model call,
    tool call and memory read — produced by `agent_runtime` (Plan 02
    Fase C). It drives the execution Timeline UI. The `total_*` and
    `*_count` columns are denormalised roll-ups of the loop's usage so a
    dashboard need not scan `steps_log`.

    Executions are NOT soft-deleted — they are an immutable audit record
    of what an agent did. A task can have several (retries).
    """

    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_task_id", "task_id"),
        Index("ix_executions_tenant_status", "tenant_id", "status"),
        CheckConstraint("iterations >= 0", name="ck_executions_iterations_non_negative"),
        CheckConstraint("total_tokens >= 0", name="ck_executions_total_tokens_non_negative"),
        CheckConstraint("total_cost_usd >= 0", name="ck_executions_total_cost_non_negative"),
    )

    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 32 chars — wide enough for 'awaiting_human_approval' (Plan 02 Fase F).
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'running'")
    )
    # Set when status='aborted' — a SafeguardCode (max_iterations_exceeded,
    # repetitive_loop_detected, …). NULL on a clean run.
    abort_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The steps_log: one dict per step (node / model_call / tool_call /
    # memory_read). Stored as JSONB so the shape can evolve migration-free.
    steps_log: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    iterations: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=6), nullable=False, server_default=text("0")
    )
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    model_call_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # --- per-call price snapshot (Plan 11 Fase C, task_11_13) --------------
    # The catalog price that was IN EFFECT when this run's model calls were
    # recorded, frozen here (and per-call in steps_log[*].price_snapshot) so
    # historical billing stays correct even after the model_prices catalog
    # changes. These columns mirror the LAST priced model call of the run
    # (the snapshot the dashboards/billing read without scanning JSONB);
    # the authoritative per-call snapshots live in steps_log. All nullable
    # / backfill-safe: pre-task runs and runs with no priced model call
    # leave them NULL (an UNKNOWN price is recorded as NULL cost, never a
    # fake 0). Canonical USD. The columns inherit executions' tenant RLS.
    price_snapshot_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    price_snapshot_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_input_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=10), nullable=True
    )
    price_output_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=10), nullable=True
    )
    price_cached_input_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=10), nullable=True
    )
    price_snapshot_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=6), nullable=True
    )


# =============================================================================
# ApprovalRequest (a human_approval_policy decision an agent is waiting on)
# =============================================================================
class ApprovalRequest(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One sensitive action an agent paused on, awaiting a human (spec §7.7).

    The approval engine (Plan 02 Fase F) intercepts an action whose
    category the project's `human_approval_policy` marks `human_required`,
    persists it here as `pending`, and parks the execution in
    `awaiting_human_approval`. A reviewer approves or rejects it; an
    unanswered request times out (task_02_27).
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_tenant_status", "tenant_id", "status"),
        Index("ix_approval_requests_execution_id", "execution_id"),
    )

    execution_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # One of the 13 sensitive-action categories (spec §7.7).
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    # The proposed action the agent paused on (tool + args, …).
    action: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    # The reviewer's optional note; on a timeout, why it expired.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


# =============================================================================
# HumanAgentConfig (the human-specific fields of an agent_type=human Agent)
# =============================================================================
class HumanAgentConfig(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """Human-specific configuration of a human agent (Plan 16 task_16_02).

    Plan 16 Decisiones Clave: ``agent_type`` extends the EXISTING Agent
    entity rather than introducing a separate one. The columns that are
    meaningless for an AI agent (who the human is, their rate, how to reach
    them, the acceptance timeout / escalation target) live here, one row per
    ``agent_type='human'`` agent, instead of widening the ``agents`` table
    with a dozen always-NULL-for-AI columns. The ``agent_id`` FK is UNIQUE so
    the relationship is strictly 1:1.

    Tenant-owned: the row carries ``tenant_id`` (TenantScopedMixin) and the DB
    enforces isolation with the SAME RLS policy shape as ``agents``
    (``{table}_tenant_isolation`` FOR ALL, ``tenant_id = NULLIF(
    current_setting('app.tenant_id', true), '')::uuid``). The
    ``assigned_user_id`` is intrinsically a tenant concept — a global Human
    Agent template MUST be forked to the tenant before it can name a User
    (Plan 16 Decisiones Clave), which is why this table is never global.

    MVP constrains ``assignment_mode`` to ``specific_user`` at the DB level
    (``ck_human_agent_config_assignment_mode``); the :class:`AssignmentMode`
    enum models the future ``role_queue`` / ``team_pool`` modes but the CHECK
    rejects them this plan.
    """

    __tablename__ = "human_agent_config"
    __table_args__ = (
        # 1:1 with the human agent — at most one config row per agent.
        UniqueConstraint("agent_id", name="uq_human_agent_config_agent"),
        Index("ix_human_agent_config_tenant_id", "tenant_id"),
        Index("ix_human_agent_config_assigned_user", "assigned_user_id"),
        # MVP: only specific_user. Model the enum, constrain the column.
        CheckConstraint(
            "assignment_mode = 'specific_user'",
            name="ck_human_agent_config_assignment_mode",
        ),
        # A non-negative rate when present (NULL = no rate configured).
        CheckConstraint(
            "hourly_rate IS NULL OR hourly_rate >= 0",
            name="ck_human_agent_config_hourly_rate_non_negative",
        ),
        # Timeouts / expected times are positive when present.
        CheckConstraint(
            "acceptance_timeout_hours > 0",
            name="ck_human_agent_config_acceptance_timeout_positive",
        ),
    )

    # The human agent this config belongs to (agent_type='human'). UNIQUE via
    # __table_args__; CASCADE so deleting the agent removes its config.
    agent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # MVP: specific_user. Stored as the :class:`AssignmentMode` value (TEXT) —
    # same string-backed-enum convention as agent_type/scope. DB-constrained
    # to 'specific_user' by ck_human_agent_config_assignment_mode.
    assignment_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'specific_user'")
    )

    # The concrete User this human agent resolves to (assignment_mode=
    # specific_user). SET NULL so the config survives a user deletion (the
    # orchestrator then surfaces an unassigned human agent). Nullable so a
    # config can be created before the User is picked.
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Cost inputs: rate + ISO-4217 currency (mirrors organizations.hourly_rate
    # / hourly_rate_currency, migration 0019). Coste humano = rate * horas
    # (imputed in Plan 16 Fase D). NULL = no rate configured yet.
    hourly_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    hourly_rate_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Preferred notification channels for the assigned user — a JSONB list of
    # channel identifiers (e.g. ["email", "in_app"]). JSONB so the shape can
    # evolve migration-free.
    notification_channels: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # How long the assigned user has to accept before escalation (Plan 16
    # Decisiones Clave: 24h default). Acceptance-timeout job lands in Fase B.
    acceptance_timeout_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("24")
    )

    # Who the task escalates to if the assigned user does not accept in time.
    # SET NULL so the config survives that user's deletion.
    escalation_target_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Planning estimates the PM agent uses (Plan 16 Fase E). NULL = unknown.
    expected_response_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_execution_time_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"HumanAgentConfig(id={self.id!r}, agent_id={self.agent_id!r},"
            f" assigned_user_id={self.assigned_user_id!r})"
        )


# =============================================================================
# HumanWorkSession (the Execution-equivalent audit trail for human tasks)
# =============================================================================
class HumanWorkSession(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One session of a human working on a task (Plan 16 task_16_03).

    Plan 16 Decisiones Clave: ``HumanWorkSession`` replaces ``Execution`` for
    ``agent_type='human'`` tasks. Where an AI task records a row in
    :class:`Execution` per run of the agent loop, a human task records a row
    here per work session — who did the work (``user_id``), when (``start_at``
    / ``end_at``), how many hours it took (``hours_logged``), free-form
    ``comments``, and the deliverables the human attached
    (``output_files_attached``). Like :class:`Execution`, sessions are NOT
    soft-deleted — they are an immutable audit record of what a human did; a
    task can have several.

    Tenant-owned: the row carries ``tenant_id`` (TenantScopedMixin) and the DB
    enforces isolation with the SAME RLS policy shape as ``executions``
    (``{table}_tenant_isolation`` FOR ALL, ``tenant_id = NULLIF(
    current_setting('app.tenant_id', true), '')::uuid``). A work session is
    intrinsically tenant-scoped (it references a ``users`` row, which is
    tenant-owned), so this table is never global.
    """

    __tablename__ = "human_work_sessions"
    __table_args__ = (
        Index("ix_human_work_sessions_tenant_id", "tenant_id"),
        # Audit-trail read path: "the sessions of this task" (mirrors
        # ix_executions_task_id, the Execution table this replaces).
        Index("ix_human_work_sessions_task_id", "task_id"),
        # Logged hours non-negative when present (NULL = not logged).
        CheckConstraint(
            "hours_logged IS NULL OR hours_logged >= 0",
            name="ck_human_work_sessions_hours_non_negative",
        ),
        # A finished session cannot end before it started.
        CheckConstraint(
            "end_at IS NULL OR end_at >= start_at",
            name="ck_human_work_sessions_end_after_start",
        ),
    )

    # The human task this session belongs to. CASCADE so deleting the task
    # removes its sessions (mirrors executions.task_id).
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The human who worked. SET NULL so the audit record survives a user
    # deletion (the session stays, the attribution is lost — same trade-off
    # executions.agent_id makes for a deleted agent). Nullable for that reason.
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # When the session began. Defaults to now() so a freshly-created session
    # is timestamped without the caller having to set it.
    start_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    # When the session finished. NULL while the human is still working.
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Optional logged hours; feeds coste humano = rate * hours (Plan 16 Fase
    # D). NULL = the human did not log hours for this session.
    hours_logged: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=8, scale=2), nullable=True
    )

    # The human's free-form notes / output text for this session.
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Deliverables the human attached — a JSONB list of attachment descriptors
    # (files, URLs, screenshots). JSONB so the shape can evolve migration-free.
    output_files_attached: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"HumanWorkSession(id={self.id!r}, task_id={self.task_id!r},"
            f" user_id={self.user_id!r})"
        )


# =============================================================================
# HumanTaskAssignment (who a human task is currently with + accept cycle)
# =============================================================================
class HumanTaskAssignment(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One assignment of a human task to a concrete User (Plan 16 task_16_05).

    When the orchestrator routes a ``ready`` task whose assignee Agent is
    ``agent_type='human'`` it does NOT request a runtime container from the
    pool (the AI path). Instead it creates one of these rows — recording the
    human Agent (``human_agent_id``) and the concrete User the work landed on
    (``assigned_to_user_id``, resolved from
    ``human_agent_config.assigned_user_id``) — and transitions the Task to
    ``assigned_to_human`` via the §7.2 state machine (task_16_04). The
    acceptance-timeout job (task_16_06) reads the ``pending_acceptance`` rows
    and, on expiry, creates a fresh assignment for the
    ``escalation_target_user_id`` (marking this one ``reassigned``).

    Tenant-owned: the row carries ``tenant_id`` (TenantScopedMixin) and the DB
    enforces isolation with the SAME RLS policy shape as ``human_work_sessions``
    / ``agents`` (``{table}_tenant_isolation`` FOR ALL, ``tenant_id = NULLIF(
    current_setting('app.tenant_id', true), '')::uuid``). An assignment is
    intrinsically tenant-scoped (it names a tenant ``users`` row and a tenant
    ``agents`` row), so this table is never global.

    Assignments are an append-only audit-style trail (no soft-delete): a task
    may accrue several rows over its life (initial assignment, escalation
    reassignment, …); ``status`` records each one's outcome.
    """

    __tablename__ = "human_task_assignments"
    __table_args__ = (
        Index("ix_human_task_assignments_tenant_id", "tenant_id"),
        # Read path: "the assignments of this task" (detail view / audit) and
        # "the live assignments of this user" (the personal inbox, Fase C).
        Index("ix_human_task_assignments_task_id", "task_id"),
        Index("ix_human_task_assignments_assigned_user", "assigned_to_user_id"),
        # The acceptance-timeout sweep (task_16_06) scans the open
        # pending_acceptance rows by age — a partial index keeps that scan
        # cheap as accepted/expired rows accumulate.
        Index(
            "ix_human_task_assignments_pending",
            "assigned_at",
            postgresql_where=text("status = 'pending_acceptance'"),
        ),
        # The DB enforces the HumanTaskAssignmentStatus value set (mirrors the
        # ck_agents_agent_type CHECK shape).
        CheckConstraint(
            "status IN ('pending_acceptance', 'accepted', 'reassigned'," " 'declined', 'expired')",
            name="ck_human_task_assignments_status",
        ),
    )

    # The human task this assignment is for. CASCADE so deleting the task
    # removes its assignments (mirrors human_work_sessions.task_id).
    task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The human Agent (agent_type='human') the task is assigned to. SET NULL so
    # the assignment record survives the agent's (soft) removal.
    human_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The concrete User the work landed on (resolved from
    # human_agent_config.assigned_user_id). SET NULL so the record survives a
    # user deletion (same trade-off human_work_sessions.user_id makes).
    assigned_to_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # When the assignment was created. Defaults to now() so a freshly-created
    # row is timestamped; the acceptance-timeout sweep ages off this column.
    assigned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    # Where this assignment is in the accept/work cycle. Stored as the
    # :class:`HumanTaskAssignmentStatus` value (TEXT) — same string-backed-enum
    # convention as agent_type/scope. DB-constrained by
    # ck_human_task_assignments_status.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending_acceptance'")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"HumanTaskAssignment(id={self.id!r}, task_id={self.task_id!r},"
            f" assigned_to_user_id={self.assigned_to_user_id!r}, status={self.status!r})"
        )


__all__ = [
    "Agent",
    "AgentRole",
    "AgentScope",
    "AgentSkill",
    "AgentSkillProficiency",
    "AgentTool",
    "AgentType",
    "ApprovalPolicyTemplate",
    "AssignmentMode",
    "ApprovalRequest",
    "ApprovalRequestStatus",
    "BudgetPeriod",
    "ChatMode",
    "Conversation",
    "Execution",
    "ExecutionStatus",
    "HumanAgentConfig",
    "HumanTaskAssignment",
    "HumanTaskAssignmentStatus",
    "HumanTaskReviewMode",
    "HumanWorkSession",
    "MemoryScope",
    "Message",
    "MessageAuthorKind",
    "Plan",
    "PlanStatus",
    "Project",
    "ProjectStatus",
    "Skill",
    "SkillCategory",
    "Task",
    "TaskComplexity",
    "TaskDependency",
    "TaskPriority",
    "TaskStatus",
    "Team",
    "TeamMember",
    "Tool",
    "ToolImplementationType",
    "ToolSecurityLevel",
]
