"""Unit tests for the Conversation/Message ORM models (Plan 03 task_03_01).

These tests do NOT hit a database. They verify the ORM declarations:

  - Both classes exist and inherit the right mixins.
  - tenant_id is present (RLS boundary).
  - The roadmap-mandated fields exist: ``mode`` on Message,
    ``attachments`` on Message, ``related_plan_id`` reachable via the
    canonical relationship (Conversation.related_plan_id + the existing
    Plan.conversation_id soft-FK).
  - Foreign keys lock the shape together (conversation -> project,
    message -> conversation, message author FKs to users/agents).
  - The author_kind <-> author_*_id table-level CHECK exists so a
    rogue INSERT cannot land in an inconsistent state.
  - Enums expose the agreed string values.

The migration test (task_03_02) is responsible for asserting Alembic
generates the tables with RLS attached.
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
from sqlalchemy import CheckConstraint, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import String, Text


# ---------------------------------------------------------------------------
# Smoke: both classes exist on `domain` (re-exported from conversation.py)
# ---------------------------------------------------------------------------
def test_conversation_class_re_exported() -> None:
    assert inspect.isclass(d.Conversation)
    assert d.Conversation.__tablename__ == "conversations"


def test_message_class_re_exported() -> None:
    assert inspect.isclass(d.Message)
    assert d.Message.__tablename__ == "messages"


# ---------------------------------------------------------------------------
# Mixin contract
# ---------------------------------------------------------------------------
def test_conversation_is_tenant_scoped_and_soft_deletable() -> None:
    assert issubclass(d.Conversation, TenantScopedMixin)
    assert issubclass(d.Conversation, UUIDPrimaryKeyMixin)
    assert issubclass(d.Conversation, TimestampMixin)
    assert issubclass(d.Conversation, SoftDeleteMixin)


def test_message_is_tenant_scoped_but_not_soft_deletable() -> None:
    # Messages are an append-only feed; we never soft-delete one.
    assert issubclass(d.Message, TenantScopedMixin)
    assert issubclass(d.Message, UUIDPrimaryKeyMixin)
    assert issubclass(d.Message, TimestampMixin)
    assert not issubclass(d.Message, SoftDeleteMixin)


# ---------------------------------------------------------------------------
# Conversation — required columns
# ---------------------------------------------------------------------------
def test_conversation_required_columns() -> None:
    cols = d.Conversation.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "project_id",
        "title",
        "current_mode",
        "custom_mode_name",
        "related_plan_id",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",
    ):
        assert name in cols, f"Conversation missing column {name}"


def test_conversation_current_mode_string_width() -> None:
    col = d.Conversation.__table__.columns["current_mode"]
    assert isinstance(col.type, String)
    assert col.type.length == 32
    assert col.nullable is False


def test_conversation_default_mode_is_planning() -> None:
    col = d.Conversation.__table__.columns["current_mode"]
    # server_default is `'planning'` (with quotes from text("'planning'"))
    assert col.server_default is not None
    assert "planning" in str(col.server_default.arg)


# ---------------------------------------------------------------------------
# Message — required columns (the roadmap mandates `mode`, `attachments`,
# and a link to Plan; here that link lives on Conversation.related_plan_id
# plus the existing soft-FK Plan.conversation_id and the per-message
# `related_plan_id` for system messages that reference a plan)
# ---------------------------------------------------------------------------
def test_message_required_columns() -> None:
    cols = d.Message.__table__.columns
    for name in (
        "id",
        "tenant_id",
        "conversation_id",
        "author_kind",
        "author_user_id",
        "author_agent_id",
        "content",
        "mode",
        "attachments",
        "related_plan_id",
        "is_summary",
        "created_at",
    ):
        assert name in cols, f"Message missing column {name}"


def test_message_mode_column_type() -> None:
    col = d.Message.__table__.columns["mode"]
    assert isinstance(col.type, String)
    assert col.type.length == 32
    assert col.nullable is False


def test_message_attachments_is_jsonb_array() -> None:
    col = d.Message.__table__.columns["attachments"]
    assert isinstance(col.type, JSONB)
    assert col.nullable is False
    # default empty list, so callers never have to coalesce
    assert "[]" in str(col.server_default.arg)


def test_message_content_is_text() -> None:
    col = d.Message.__table__.columns["content"]
    assert isinstance(col.type, Text)


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------
def _fk_targets(col: Column) -> set[str]:
    return {fk.target_fullname for fk in col.foreign_keys}


def test_conversation_foreign_keys() -> None:
    # conversation -> project (CASCADE so deleting a project wipes its chats)
    assert "projects.id" in _fk_targets(d.Conversation.__table__.c.project_id)
    # conversation -> users (SET NULL, the user may be deleted while
    # leaving the chat history intact)
    assert "users.id" in _fk_targets(d.Conversation.__table__.c.created_by)
    # related_plan_id -> plans (SET NULL). Esta aserción decía lo contrario
    # hasta el 2026-08-20: «soft-FK a propósito, sin constraint formal a nivel
    # ORM». Era el §2 de `verificar-antes-de-implementar` en estado puro —
    # documentaba lo observado y lo convertía en contrato. La migración 0014 SÍ
    # crea la FK (`fk_conversations_related_plan_id`), así que el modelo callado
    # no era un diseño: era la razón de que `alembic check` propusiera BORRARLA.
    assert "plans.id" in _fk_targets(d.Conversation.__table__.c.related_plan_id)


def test_message_foreign_keys() -> None:
    assert "conversations.id" in _fk_targets(d.Message.__table__.c.conversation_id)
    assert "users.id" in _fk_targets(d.Message.__table__.c.author_user_id)
    assert "agents.id" in _fk_targets(d.Message.__table__.c.author_agent_id)
    # Misma historia que en `Conversation`: la 0014 promueve también ésta.
    assert "plans.id" in _fk_targets(d.Message.__table__.c.related_plan_id)


def test_the_0014_foreign_keys_carry_their_deployed_name_and_ondelete() -> None:
    """Las tres FK que la 0014 promovió, con nombre y `ondelete` reales.

    No basta con que exista la constraint: `alembic check` empareja claves
    ajenas por (origen, destino, `onupdate`, `ondelete`, deferrable), así que un
    `ondelete` distinto del desplegado deja el item de deriva abierto igual —
    proponiendo BORRAR la de producción. Y el `SET NULL` no es un detalle: es lo
    que hace que borrar un plan deje el mensaje huérfano en vez de bloquear el
    borrado.
    """
    from sqlalchemy import ForeignKeyConstraint

    def _fk(table, name: str) -> ForeignKeyConstraint:
        found = [
            c for c in table.constraints if isinstance(c, ForeignKeyConstraint) and c.name == name
        ]
        assert found, (
            f"falta {name} en {table.name}: {sorted(str(c.name) for c in table.constraints)}"
        )
        return found[0]

    conv = _fk(d.Conversation.__table__, "fk_conversations_related_plan_id")
    assert conv.ondelete == "SET NULL"
    # `use_alter` rompe el ciclo `plans` <-> `conversations` al ordenar tablas;
    # sin él SQLAlchemy avisa y DESCARTA las dos FK.
    assert conv.use_alter is True

    msg = _fk(d.Message.__table__, "fk_messages_related_plan_id")
    assert msg.ondelete == "SET NULL"


# ---------------------------------------------------------------------------
# Table-level CHECK constraints — invariants enforced at INSERT time
# ---------------------------------------------------------------------------
def _check_names(table) -> set[str]:
    return {c.name for c in table.constraints if isinstance(c, CheckConstraint) and c.name}


def test_conversation_custom_mode_check_present() -> None:
    assert "ck_conversations_custom_mode_name_consistency" in _check_names(d.Conversation.__table__)


def test_message_author_kind_check_present() -> None:
    assert "ck_messages_author_kind_consistency" in _check_names(d.Message.__table__)


# ---------------------------------------------------------------------------
# Enums — frozen value sets
# ---------------------------------------------------------------------------
def test_chat_mode_values() -> None:
    assert {m.value for m in d.ChatMode} == {
        "planning",
        "discussion",
        "execution",
        "custom",
    }


def test_message_author_kind_values() -> None:
    assert {k.value for k in d.MessageAuthorKind} == {"user", "agent", "system"}


# ---------------------------------------------------------------------------
# Indexes — the ones critical for chat UX (load by project, ordered feed)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "table, ix",
    [
        (d.Conversation.__table__, "ix_conversations_tenant_project"),
        (d.Conversation.__table__, "ix_conversations_related_plan"),
        (d.Message.__table__, "ix_messages_conversation_id"),
        (d.Message.__table__, "ix_messages_tenant_id"),
    ],
)
def test_critical_indexes_present(table, ix: str) -> None:
    assert ix in {i.name for i in table.indexes}
