"""Unit tests for the MemoryEntry ORM model (Plan 04 task_04_01).

The model itself is exercised end-to-end against Postgres + pgvector
in `tests/integration/test_memory_migration.py` (task_04_02). Here we
stay in-process and verify the contract:

  - the enums (:class:`MemoryScope`, :class:`MemoryType`) carry the
    expected values,
  - the ORM class is registered on the metadata with the column shape
    the rest of Plan 04 will rely on,
  - the CHECK constraints encode the scope→pointer pairing,
  - the embedding column has the right pgvector dimensionality.
"""

from __future__ import annotations

import pytest
from api_server.db.domain import MemoryScope, MemoryType
from api_server.db.memory import EMBEDDING_DIM, MemoryEntry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
def test_memory_scope_has_four_canonical_values() -> None:
    assert {s.value for s in MemoryScope} == {
        "private",
        "team_shared",
        "project_shared",
        "global",
    }


def test_memory_type_has_episodic_and_semantic() -> None:
    assert {t.value for t in MemoryType} == {"episodic", "semantic"}


# ---------------------------------------------------------------------------
# Table shape
# ---------------------------------------------------------------------------
def test_memory_entries_table_name_is_canonical() -> None:
    assert MemoryEntry.__tablename__ == "memory_entries"


def test_memory_entries_has_required_columns() -> None:
    cols = {c.name for c in MemoryEntry.__table__.columns}
    # Owner-pointer trio + scope + type + content + embedding + back-links.
    assert {
        "id",
        "tenant_id",
        "scope",
        "type",
        "content",
        "embedding",
        "user_id",
        "team_id",
        "project_id",
        "source_execution_id",
        "agent_id",
        "tags",
        "metadata",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_metadata_column_is_metadata_not_metadata_underscore() -> None:
    """Pydantic / SA reserves `metadata` as an attribute name; the model
    uses ``metadata_`` in Python but the column itself must be named
    ``metadata`` so SQL and JSON serialisation are idiomatic."""
    col = MemoryEntry.__table__.columns["metadata"]
    assert col is not None


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
def test_embedding_dim_matches_default_ollama_model() -> None:
    # `nomic-embed-text-v1.5` (Ollama default) emits 768-dim vectors.
    assert EMBEDDING_DIM == 768


def test_embedding_column_is_nullable() -> None:
    """The Memorizer back-fills the embedding asynchronously, so the
    column must accept NULL on insert."""
    col = MemoryEntry.__table__.columns["embedding"]
    assert col.nullable is True


def test_embedding_column_uses_pgvector_with_correct_dim() -> None:
    col = MemoryEntry.__table__.columns["embedding"]
    # pgvector.sqlalchemy.Vector exposes the dimensionality on the type.
    assert getattr(col.type, "dim", None) == EMBEDDING_DIM


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------
def _check_constraint_names() -> set[str]:
    from sqlalchemy import CheckConstraint

    return {c.name for c in MemoryEntry.__table__.constraints if isinstance(c, CheckConstraint)}


def test_scope_pointer_check_constraint_is_present() -> None:
    """A `private` entry must carry a user_id, a `team_shared` one a
    team_id, etc. The constraint name comes from the model module."""
    assert "ck_memory_entries_scope_pointer" in _check_constraint_names()


def test_scope_value_check_constraint_is_present() -> None:
    assert "ck_memory_entries_scope" in _check_constraint_names()


def test_type_value_check_constraint_is_present() -> None:
    assert "ck_memory_entries_type" in _check_constraint_names()


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------
def test_tenant_scope_type_index_is_present() -> None:
    """Hot path: filter by tenant+scope+type when serving `memory_recall`."""
    idx_names = {idx.name for idx in MemoryEntry.__table__.indexes}
    assert "ix_memory_entries_tenant_scope_type" in idx_names


def test_per_owner_partial_indexes_are_present() -> None:
    idx_names = {idx.name for idx in MemoryEntry.__table__.indexes}
    assert {
        "ix_memory_entries_project_id",
        "ix_memory_entries_team_id",
        "ix_memory_entries_user_id",
    } <= idx_names


def test_cortex_forgetting_sweep_index_is_in_the_model_too() -> None:
    """The index migration 0142 creates must ALSO be declared in the ORM.

    The behaviour is tested where behaviour lives — against a real Postgres, in
    ``tests/integration/test_cortex_forget_sweep_index.py``, which checks the
    planner actually picks it and stops sorting. What this unit test buys is the
    other half: that model and schema do not drift. A migration whose index is
    missing from ``__table_args__`` is invisible to anything that builds the
    schema from metadata, and nothing else in the suite would notice.

    Column ORDER is asserted on purpose: ``(user_id, created_at)`` is what lets
    the sweep's ``ORDER BY created_at ... LIMIT`` be served straight from the
    index. Drop ``created_at`` and the plan goes back to sorting the owner's
    whole live private memory before the limit applies.
    """
    by_name = {idx.name: idx for idx in MemoryEntry.__table__.indexes}
    index = by_name.get("ix_memory_entries_cortex_sweep")
    assert index is not None, "migration 0142's index is missing from the model"
    assert [c.name for c in index.columns] == ["user_id", "created_at"]

    # Partial, and on the four conditions the sweep holds constant. The DB column
    # behind ``metadata_`` is named ``metadata`` -- see the migration's docstring.
    predicate = str(index.dialect_options["postgresql"]["where"])
    for condition in ("deleted_at IS NULL", "'private'", "'episodic'", "metadata ->> 'cortex'"):
        assert condition in predicate, f"missing {condition!r} in the predicate: {predicate}"
