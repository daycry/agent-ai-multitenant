"""Phase-1 domain schema: agents, skills, tools, teams, projects, plans, tasks.

Eleven tables that materialize the ORM declared in
`api_server.db.domain` (task_01_01). RLS is turned on for every
tenant-scoped top-level table (agents/skills/tools/teams/projects/
plans/tasks); junctions rely on parent visibility via ON DELETE
CASCADE.

Same policy shape as phase 0:

  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)

Down-migration drops everything in reverse FK order. Idempotent
round-trip is asserted by `tests/integration/test_migrations_v2.py`.

Revision ID: 0002_domain_minimum
Revises: 0001_initial
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_domain_minimum"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# RLS DDL emitted as raw SQL (Alembic ops don't model row-level security).
# Statements are kept as a tuple so asyncpg sends them one prepared
# statement at a time -- it refuses multi-statement strings.
# ---------------------------------------------------------------------------
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "agents",
    "skills",
    "tools",
    "teams",
    "projects",
    "plans",
    "tasks",
)


def _rls_up() -> tuple[str, ...]:
    stmts: list[str] = []
    for table in _TENANT_SCOPED_TABLES:
        stmts.extend(
            [
                f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
                f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL"
                " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
            ]
        )
    return tuple(stmts)


def _rls_down() -> tuple[str, ...]:
    stmts: list[str] = []
    for table in reversed(_TENANT_SCOPED_TABLES):
        stmts.extend(
            [
                f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}",
                f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
            ]
        )
    return tuple(stmts)


# ---------------------------------------------------------------------------
# Shared column factories — every tenant-scoped top-level table gets the
# same five tail columns (tenant_id + audit timestamps + soft-delete).
# ---------------------------------------------------------------------------
def _common_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "deleted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    ]


def _id_column() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # agents
    # -----------------------------------------------------------------------
    op.create_table(
        "agents",
        _id_column(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column(
            "agent_type",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ai'"),
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column(
            "model_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "memory_scope",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'private'"),
        ),
        sa.Column(
            "review_capability",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "max_concurrent_tasks",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "is_template",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_common_columns(),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])
    op.create_index(
        "ix_agents_tenant_role",
        "agents",
        ["tenant_id", "role"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # skills
    # -----------------------------------------------------------------------
    op.create_table(
        "skills",
        _id_column(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prompt_fragment", sa.Text(), nullable=False),
        sa.Column(
            "required_tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        *_common_columns(),
    )
    op.create_index("ix_skills_tenant_id", "skills", ["tenant_id"])
    op.create_index(
        "ix_skills_tenant_category",
        "skills",
        ["tenant_id", "category"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # tools
    # -----------------------------------------------------------------------
    op.create_table(
        "tools",
        _id_column(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column(
            "input_schema",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "output_schema",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("implementation_type", sa.String(length=32), nullable=False),
        sa.Column("implementation_ref", sa.String(length=500), nullable=True),
        sa.Column(
            "security_level",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'safe'"),
        ),
        sa.Column(
            "timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("60"),
        ),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_tools_timeout_positive"),
        *_common_columns(),
    )
    op.create_index("ix_tools_tenant_id", "tools", ["tenant_id"])
    op.create_index(
        "ix_tools_tenant_category",
        "tools",
        ["tenant_id", "category"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # agent_skills (M:N junction)
    # -----------------------------------------------------------------------
    op.create_table(
        "agent_skills",
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proficiency",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("agent_id", "skill_id", name="pk_agent_skills"),
    )

    # -----------------------------------------------------------------------
    # agent_tools (M:N junction)
    # -----------------------------------------------------------------------
    op.create_table(
        "agent_tools",
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tool_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("config_override", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("agent_id", "tool_id", name="pk_agent_tools"),
    )

    # -----------------------------------------------------------------------
    # teams
    # -----------------------------------------------------------------------
    op.create_table(
        "teams",
        _id_column(),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_workflow_template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("shared_memory_namespace", sa.String(length=120), nullable=True),
        *_common_columns(),
    )
    op.create_index("ix_teams_tenant_id", "teams", ["tenant_id"])
    op.create_index(
        "ix_teams_tenant_name",
        "teams",
        ["tenant_id", "name"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # team_members (M:N junction)
    # -----------------------------------------------------------------------
    op.create_table(
        "team_members",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_in_team", sa.String(length=64), nullable=True),
        sa.Column(
            "is_team_leader",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "assignment_priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("team_id", "agent_id", name="pk_team_members"),
    )

    # -----------------------------------------------------------------------
    # projects
    # -----------------------------------------------------------------------
    op.create_table(
        "projects",
        _id_column(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mcp_servers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rag_knowledge_bases",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "worker_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("repository_config", postgresql.JSONB(), nullable=True),
        sa.Column("human_approval_policy", postgresql.JSONB(), nullable=True),
        sa.Column("secrets_vault_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("budget_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("budget_currency", sa.String(length=3), nullable=True),
        sa.Column("budget_period", sa.String(length=16), nullable=True),
        sa.Column("budget_period_start_day", sa.Integer(), nullable=True),
        sa.Column("budget_period_length_days", sa.Integer(), nullable=True),
        sa.Column(
            "paused_by_budget",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.CheckConstraint(
            "budget_amount IS NULL OR budget_amount >= 0",
            name="ck_projects_budget_non_negative",
        ),
        *_common_columns(),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_index(
        "ix_projects_tenant_status",
        "projects",
        ["tenant_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # plans
    # -----------------------------------------------------------------------
    op.create_table(
        "plans",
        _id_column(),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        *_common_columns(),
    )
    op.create_index("ix_plans_tenant_id", "plans", ["tenant_id"])
    op.create_index(
        "ix_plans_tenant_project_status",
        "plans",
        ["tenant_id", "project_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # -----------------------------------------------------------------------
    # tasks (no soft-delete; uses terminal statuses instead)
    # -----------------------------------------------------------------------
    op.create_table(
        "tasks",
        _id_column(),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'backlog'"),
        ),
        sa.Column(
            "priority",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'medium'"),
        ),
        sa.Column(
            "assigned_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reviewer_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "acceptance_criteria",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "inputs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("estimated_complexity", sa.String(length=4), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("retry_count >= 0", name="ck_tasks_retry_count_non_negative"),
        sa.CheckConstraint("max_retries >= 0", name="ck_tasks_max_retries_non_negative"),
    )
    op.create_index("ix_tasks_tenant_id", "tasks", ["tenant_id"])
    op.create_index("ix_tasks_tenant_status", "tasks", ["tenant_id", "status"])
    op.create_index("ix_tasks_project_plan", "tasks", ["project_id", "plan_id"])

    # -----------------------------------------------------------------------
    # task_dependencies (self-M:N)
    # -----------------------------------------------------------------------
    op.create_table(
        "task_dependencies",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "depends_on_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("task_id", "depends_on_task_id", name="pk_task_dependencies"),
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
        sa.CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_task_dependencies_no_self_loop",
        ),
    )

    # -----------------------------------------------------------------------
    # RLS policies — applied last so the tables exist.
    # -----------------------------------------------------------------------
    for stmt in _rls_up():
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (policies depend on the tables).
    for stmt in _rls_down():
        op.execute(stmt)

    # Reverse FK dependency order.
    op.drop_table("task_dependencies")
    op.drop_table("tasks")
    op.drop_table("plans")
    op.drop_table("projects")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("agent_tools")
    op.drop_table("agent_skills")
    op.drop_table("tools")
    op.drop_table("skills")
    op.drop_table("agents")
