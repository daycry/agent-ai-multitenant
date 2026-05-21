"""Unit tests for the Agent linked-vs-forked extension (task_01_03).

These verify the ORM-side declaration of:

  - AgentScope enum (global_builtin / global_tenant_template / project_local)
  - new Agent columns: scope, project_id, forked_from_agent_id,
    forked_from_version, anchored_version
  - the self-FK on forked_from_agent_id resolves to agents.id
  - the FK on project_id targets projects.id
  - the CHECK constraint ck_agents_scope_project_consistency is declared
    on the table (enforced at DB level by migration 0003)

The migration test (task_01_02 / future) is responsible for asserting
the CHECK actually fires at INSERT time.
"""

from __future__ import annotations

from api_server.db import domain as d
from sqlalchemy.types import String


# ---------------------------------------------------------------------------
# AgentScope enum
# ---------------------------------------------------------------------------
def test_agent_scope_values() -> None:
    assert {s.value for s in d.AgentScope} == {
        "global_builtin",
        "global_tenant_template",
        "project_local",
    }


def test_agent_scope_string_members() -> None:
    """StrEnum -- comparing against the literal must work in both
    directions (catches accidental change to plain Enum)."""
    assert d.AgentScope.GLOBAL_BUILTIN == "global_builtin"
    assert d.AgentScope.PROJECT_LOCAL == "project_local"


# ---------------------------------------------------------------------------
# New columns
# ---------------------------------------------------------------------------
def test_agent_has_scope_column() -> None:
    col = d.Agent.__table__.columns["scope"]
    assert isinstance(col.type, String)
    assert col.nullable is False
    # Default 'project_local' so existing rows (none, but the next phases
    # may run migrations against partially-populated dev DBs) get a sane
    # value without manual backfill.
    assert col.server_default is not None


def test_agent_has_project_id_column() -> None:
    col = d.Agent.__table__.columns["project_id"]
    assert col.nullable is True, "project_id must be NULL for global_* agents"
    targets = {fk.target_fullname for fk in col.foreign_keys}
    assert "projects.id" in targets


def test_agent_has_forked_from_agent_id_self_fk() -> None:
    col = d.Agent.__table__.columns["forked_from_agent_id"]
    assert col.nullable is True
    targets = {fk.target_fullname for fk in col.foreign_keys}
    assert "agents.id" in targets, "forked_from_agent_id should be a self-referential FK"


def test_agent_has_version_columns() -> None:
    cols = d.Agent.__table__.columns
    assert "forked_from_version" in cols
    assert "anchored_version" in cols
    assert cols["forked_from_version"].nullable is True
    assert cols["anchored_version"].nullable is True


# ---------------------------------------------------------------------------
# CHECK constraint (table-level invariant)
# ---------------------------------------------------------------------------
def test_scope_project_consistency_check_is_declared() -> None:
    """The CHECK must live on the table so even raw SQL inserts can't
    create an invalid (scope, project_id) combination."""
    constraint_names = {c.name for c in d.Agent.__table__.constraints}
    assert "ck_agents_scope_project_consistency" in constraint_names


# ---------------------------------------------------------------------------
# ORM round-trip: assigning the new fields doesn't trigger errors before
# we hit the DB (purely Python-side validation that mappings line up).
# ---------------------------------------------------------------------------
def test_agent_instance_accepts_new_fields() -> None:
    """No DB hit -- just verifies the constructor accepts the new kwargs."""
    a = d.Agent(
        tenant_id="11111111-1111-1111-1111-111111111111",
        name="Backend Senior",
        role="backend_dev",
        system_prompt="You are a senior backend engineer.",
        scope="project_local",
        project_id="22222222-2222-2222-2222-222222222222",
        forked_from_agent_id="33333333-3333-3333-3333-333333333333",
        forked_from_version="1.2.0",
        anchored_version="1.2.0",
    )
    assert a.scope == "project_local"
    assert str(a.project_id) == "22222222-2222-2222-2222-222222222222"
    assert a.forked_from_version == "1.2.0"
    assert a.anchored_version == "1.2.0"
