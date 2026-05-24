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


# =============================================================================
# Enums (StrEnum so values are stable strings persisted as TEXT)
# =============================================================================
class AgentType(enum.StrEnum):
    AI = "ai"
    HUMAN = "human"


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


class BudgetPeriod(enum.StrEnum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class PlanStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskStatus(enum.StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
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


class ApprovalRequestStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


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
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

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
        CheckConstraint("timeout_seconds > 0", name="ck_tools_timeout_positive"),
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
    worker_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    repository_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    human_approval_policy: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

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
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))

    # Soft-FK to a future conversations table (Plan 03 - chat sessions).
    conversation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

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


__all__ = [
    "ApprovalRequest",
    "ApprovalRequestStatus",
    "Agent",
    "AgentRole",
    "AgentScope",
    "AgentSkill",
    "AgentSkillProficiency",
    "AgentTool",
    "AgentType",
    "ApprovalPolicyTemplate",
    "BudgetPeriod",
    "Execution",
    "ExecutionStatus",
    "MemoryScope",
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
