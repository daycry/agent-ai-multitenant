"""Unit tests for the Phase-1 domain models (task_01_01).

These tests do NOT hit a database. They verify the ORM declarations:

  - All 11 model classes import cleanly.
  - Tables have the expected names.
  - Tenant-scoped models carry `tenant_id` (the multi-tenancy boundary).
  - Junction tables expose composite primary keys (no implicit surrogate).
  - Required spec fields are present with the right column types/defaults.
  - Enums expose the agreed string values; renaming one is a contract
    break for any future seeded row that references them.

The migration test (task_01_02) is responsible for asserting Alembic
generates these tables with RLS attached.
"""

from __future__ import annotations

import inspect

import pytest
from api_server.db import domain as d
from api_server.db.base import (
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Integer, Numeric, String

# ---------------------------------------------------------------------------
# Smoke: all eleven classes exist and have the expected __tablename__
# ---------------------------------------------------------------------------
EXPECTED_TABLES = {
    "Agent": "agents",
    "Skill": "skills",
    "Tool": "tools",
    "AgentSkill": "agent_skills",
    "AgentTool": "agent_tools",
    "Team": "teams",
    "TeamMember": "team_members",
    "Project": "projects",
    "Plan": "plans",
    "Task": "tasks",
    "TaskDependency": "task_dependencies",
}


@pytest.mark.parametrize("cls_name,table", list(EXPECTED_TABLES.items()))
def test_model_class_has_expected_tablename(cls_name: str, table: str) -> None:
    cls = getattr(d, cls_name)
    assert inspect.isclass(cls)
    assert cls.__tablename__ == table


# ---------------------------------------------------------------------------
# Mixin contract: tenant-scoped (top-level) entities carry tenant_id + uuid PK
# ---------------------------------------------------------------------------
TENANT_SCOPED = [d.Agent, d.Skill, d.Tool, d.Team, d.Project, d.Plan, d.Task]


@pytest.mark.parametrize("model", TENANT_SCOPED)
def test_top_level_models_are_tenant_scoped(model) -> None:
    assert issubclass(
        model, TenantScopedMixin
    ), f"{model.__name__} must inherit TenantScopedMixin for RLS"
    assert issubclass(model, UUIDPrimaryKeyMixin)
    assert issubclass(model, TimestampMixin)


@pytest.mark.parametrize("model", [d.Agent, d.Skill, d.Tool, d.Team, d.Project, d.Plan])
def test_top_level_models_are_soft_deletable(model) -> None:
    """Task is the deliberate exception (terminal states do the work)."""
    assert issubclass(model, SoftDeleteMixin), f"{model.__name__} should be soft-deletable"


def test_task_is_not_soft_deleted() -> None:
    """Tasks reach `done` / `cancelled` instead of a soft-delete marker.
    If product later wants soft-delete on tasks, add the mixin there."""
    assert not issubclass(d.Task, SoftDeleteMixin)


# ---------------------------------------------------------------------------
# Junction tables: composite PK + tenant_id propio (defensa en profundidad)
#
# Esta invariante se INVIRTIÓ el 2026-07-30 con el plan prod-14 y la migración
# `0124_junction_tenant_rls`. Antes decía lo contrario —«no llevan tenant_id,
# los padres ya son tenant-scoped»— y era cierto pero insuficiente: sin columna
# propia no hay política RLS que las cubra, así que la única defensa era que
# TODA query pasara por el padre. Un servicio con BYPASSRLS (o un JOIN mal
# escrito) se saltaba el aislamiento sin que la base de datos dijera nada.
#
# Ahora cada fila lleva su `tenant_id`, un trigger lo DERIVA del padre y rechaza
# cualquier valor contradictorio, y la RLS va con FORCE. El test cambia de signo
# a propósito: exigir la columna es lo que hace que la próxima tabla de unión no
# nazca desprotegida.
# ---------------------------------------------------------------------------
JUNCTIONS = {
    d.AgentSkill: ("agent_id", "skill_id"),
    d.AgentTool: ("agent_id", "tool_id"),
    d.TeamMember: ("team_id", "agent_id"),
    d.TaskDependency: ("task_id", "depends_on_task_id"),
}


@pytest.mark.parametrize("model,pk_cols", list(JUNCTIONS.items()))
def test_junctions_have_composite_pk(model, pk_cols) -> None:
    """La PK sigue siendo la pareja de FKs: `tenant_id` es defensa, no identidad."""
    cols = model.__table__.primary_key.columns
    assert {c.name for c in cols} == set(
        pk_cols
    ), f"{model.__name__} expected PK {pk_cols}, got {[c.name for c in cols]}"


@pytest.mark.parametrize("model", list(JUNCTIONS.keys()))
def test_junctions_carry_tenant_id_for_rls(model) -> None:
    """Sin columna propia no hay política RLS posible (prod-14, migración 0124)."""
    assert "tenant_id" in model.__table__.columns, (
        f"{model.__name__} no lleva tenant_id: la RLS no puede cubrirla y su "
        "aislamiento vuelve a depender de que ninguna query la lea sin el padre"
    )


@pytest.mark.parametrize("model", list(JUNCTIONS.keys()))
def test_junction_tenant_id_is_not_nullable(model) -> None:
    """Una fila con `tenant_id` NULL no la ve ninguna política: sería un agujero
    silencioso justo en la tabla que acabamos de blindar."""
    assert not model.__table__.columns["tenant_id"].nullable, (
        f"{model.__name__}.tenant_id es nullable: una fila con NULL se escapa de "
        "la política RLS en vez de ser rechazada"
    )


# ---------------------------------------------------------------------------
# Agent — spec §4.2.1 fields
# ---------------------------------------------------------------------------
def test_agent_required_columns() -> None:
    cols = d.Agent.__table__.columns
    for name in (
        "name",
        "description",
        "avatar_url",
        "agent_type",
        "role",
        "system_prompt",
        "model_config",
        "memory_scope",
        "review_capability",
        "max_concurrent_tasks",
        "is_template",
    ):
        assert name in cols, f"Agent missing column {name}"
    assert isinstance(cols["model_config"].type, JSONB)
    assert isinstance(cols["max_concurrent_tasks"].type, Integer)


# ---------------------------------------------------------------------------
# Tool — spec §3.1.5
# ---------------------------------------------------------------------------
def test_tool_required_columns() -> None:
    cols = d.Tool.__table__.columns
    for name in (
        "name",
        "description",
        "category",
        "input_schema",
        "output_schema",
        "implementation_type",
        "implementation_ref",
        "security_level",
        "timeout_seconds",
        "rate_limit_per_minute",
    ):
        assert name in cols, f"Tool missing column {name}"
    assert isinstance(cols["input_schema"].type, JSONB)
    assert isinstance(cols["output_schema"].type, JSONB)


# ---------------------------------------------------------------------------
# Project — spec §3.1.7 (with budget §28.7)
# ---------------------------------------------------------------------------
def test_project_required_columns() -> None:
    cols = d.Project.__table__.columns
    for name in (
        "name",
        "description",
        "status",
        "team_id",
        "mcp_servers",
        "rag_knowledge_bases",
        "worker_config",
        "repository_config",
        "human_approval_policy",
        "secrets_vault_id",
        "budget_amount",
        "budget_currency",
        "budget_period",
        "paused_by_budget",
    ):
        assert name in cols, f"Project missing column {name}"
    assert isinstance(cols["budget_amount"].type, Numeric)


# ---------------------------------------------------------------------------
# Task — spec §4.2.3
# ---------------------------------------------------------------------------
def test_task_required_columns() -> None:
    cols = d.Task.__table__.columns
    for name in (
        "project_id",
        "plan_id",
        "title",
        "description",
        "status",
        "priority",
        "assigned_agent_id",
        "reviewer_agent_id",
        "acceptance_criteria",
        "inputs",
        "estimated_complexity",
        "retry_count",
        "max_retries",
        "started_at",
        "completed_at",
    ):
        assert name in cols, f"Task missing column {name}"
    assert isinstance(cols["acceptance_criteria"].type, JSONB)
    assert isinstance(cols["inputs"].type, JSONB)
    # title is the only string with a hard length limit per spec
    assert isinstance(cols["title"].type, String)
    assert cols["title"].type.length == 200


# ---------------------------------------------------------------------------
# Foreign keys — the ones that lock the domain shape together
# ---------------------------------------------------------------------------
def _fk_targets(col: Column) -> set[str]:
    return {fk.target_fullname for fk in col.foreign_keys}


def test_critical_foreign_keys() -> None:
    # tasks -> projects, plans
    assert "projects.id" in _fk_targets(d.Task.__table__.c.project_id)
    assert "plans.id" in _fk_targets(d.Task.__table__.c.plan_id)
    # tasks.assigned_agent_id, reviewer_agent_id -> agents
    assert "agents.id" in _fk_targets(d.Task.__table__.c.assigned_agent_id)
    assert "agents.id" in _fk_targets(d.Task.__table__.c.reviewer_agent_id)
    # plans -> projects, users
    assert "projects.id" in _fk_targets(d.Plan.__table__.c.project_id)
    assert "users.id" in _fk_targets(d.Plan.__table__.c.created_by)
    # projects.team_id -> teams
    assert "teams.id" in _fk_targets(d.Project.__table__.c.team_id)
    # junctions
    assert "agents.id" in _fk_targets(d.AgentSkill.__table__.c.agent_id)
    assert "skills.id" in _fk_targets(d.AgentSkill.__table__.c.skill_id)
    assert "agents.id" in _fk_targets(d.AgentTool.__table__.c.agent_id)
    assert "tools.id" in _fk_targets(d.AgentTool.__table__.c.tool_id)
    assert "teams.id" in _fk_targets(d.TeamMember.__table__.c.team_id)
    assert "agents.id" in _fk_targets(d.TeamMember.__table__.c.agent_id)
    # task_dependencies self-FK
    assert "tasks.id" in _fk_targets(d.TaskDependency.__table__.c.task_id)
    assert "tasks.id" in _fk_targets(d.TaskDependency.__table__.c.depends_on_task_id)


def test_task_dependency_blocks_self_loop() -> None:
    """The CHECK constraint must be on the table -- catches a programmer
    typo at INSERT time, not application-only."""
    constraint_names = {c.name for c in d.TaskDependency.__table__.constraints}
    assert "ck_task_dependencies_no_self_loop" in constraint_names


# ---------------------------------------------------------------------------
# Enums — frozen value sets (changing a value breaks persisted rows)
# ---------------------------------------------------------------------------
def test_agent_role_values() -> None:
    assert {r.value for r in d.AgentRole} == {
        "project_manager",
        "architect",
        "backend_dev",
        "frontend_dev",
        "qa",
        "reviewer",
        "leader",
        "worker",
        "specialist",
        "researcher",
        "devops",
        "security",
        "technical_writer",
        "custom",
    }


def test_memory_scope_values() -> None:
    assert {s.value for s in d.MemoryScope} == {
        "private",
        "team_shared",
        "project_shared",
        "global",
    }


def test_tool_implementation_type_values() -> None:
    assert {t.value for t in d.ToolImplementationType} == {
        "builtin",
        "python_function",
        "http_endpoint",
        "mcp_tool",
        "docker_command",
    }


def test_tool_security_level_values() -> None:
    assert {s.value for s in d.ToolSecurityLevel} == {
        "safe",
        "sandboxed",
        "privileged",
    }


def test_task_status_values() -> None:
    assert {s.value for s in d.TaskStatus} == {
        "backlog",
        "ready",
        # Plan 16 §7.2 — tarea asignada a un Human Agent, pendiente de
        # aceptación por el User asignado (task_16_04).
        "assigned_to_human",
        "in_progress",
        # ADR 0020 — tarea aparcada esperando decisión humana.
        "awaiting_human_approval",
        "in_review",
        "blocked",
        "done",
        "cancelled",
    }


def test_task_priority_values() -> None:
    assert {p.value for p in d.TaskPriority} == {"low", "medium", "high", "critical"}


def test_task_complexity_values() -> None:
    assert {c.value for c in d.TaskComplexity} == {"xs", "s", "m", "l", "xl"}


def test_plan_status_values() -> None:
    # Plan 03 task_03_16 widened the lifecycle; task_03_25 added the
    # extra `pending_second_approval` state for the double-firma flow.
    assert {s.value for s in d.PlanStatus} == {
        "pending_approval",
        "pending_second_approval",
        "draft",
        "approved",
        "in_progress",
        "blocked",
        "pending_human_validation",
        "completed",
        "cancelled",
        "rejected",
        "archived",
    }


def test_project_status_values() -> None:
    assert {s.value for s in d.ProjectStatus} == {"active", "paused", "archived"}


def test_budget_period_values() -> None:
    assert {p.value for p in d.BudgetPeriod} == {
        "weekly",
        "monthly",
        "quarterly",
        "yearly",
        "custom",
    }


def test_agent_type_values() -> None:
    assert {t.value for t in d.AgentType} == {"ai", "human"}


def test_agent_skill_proficiency_values() -> None:
    assert {p.value for p in d.AgentSkillProficiency} == {"basic", "standard", "expert"}
