"""Córtex conversational threads (córtex F1, ADR 0074).

The two FIRST **tenant-less** tables of the platform (a conscious exception
to Principle 1 — RLS): the System Owner's córtex is a singleton, so its
conversation history is NOT scoped by ``tenant_id`` + RLS. Isolation is by an
**explicit ``owner_user_id`` filter on every SQL statement** (defence in
depth — there is no RLS to fall back on; see ``cortex/threads.py`` and the
mandatory cross-owner test of F1).

- :class:`CortexConversation` — a persistent thread the owner holds with the
  córtex (unlike the tenant assistant, whose chat is stateless). Soft-deletable.
  Carries a real ``tenant_id`` (Decisión D1) purely as the **physical
  discriminator** the owner's memory (``memory_entries``, which requires
  ``tenant_id NOT NULL``) needs — NOT an authorisation axis.
- :class:`CortexTurn` — one immutable entry in the thread (``user`` or
  ``cortex``). ``owner_user_id`` is duplicated here on purpose so the
  isolation filter never needs a join.

Style mirrors :mod:`api_server.db.conversation` and :mod:`api_server.db.memory`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# CortexConversation — a persistent owner thread (tenant-less, owner-scoped)
# =============================================================================
class CortexConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A persistent córtex thread owned by the System Owner.

    NOT :class:`TenantScopedMixin` — there is no RLS here. ``tenant_id`` is a
    plain column (the memory's physical discriminator, Decisión D1), and
    ``owner_user_id`` is the real isolation axis enforced by explicit SQL
    filters in :mod:`api_server.cortex.threads`.
    """

    __tablename__ = "cortex_conversations"
    __table_args__ = (
        # Listing "the owner's live threads, most-recent first". Partial so
        # soft-deleted threads drop out of the index.
        Index(
            "ix_cortex_conversations_owner",
            "owner_user_id",
            text("updated_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    # The thread owner — the isolation filter (see module docstring).
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Physical discriminator the owner's memory needs (Decisión D1); NOT an
    # authorisation axis. Resolved once as the owner's oldest active membership.
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
    )
    # Auto-labelled from the first message; editable later.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The effective catalog model-id of the creating turn (audit / UI).
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"CortexConversation(id={self.id!r}, owner_user_id={self.owner_user_id!r},"
            f" tenant_id={self.tenant_id!r})"
        )


# =============================================================================
# CortexTurn — one immutable entry in a thread (no soft-delete)
# =============================================================================
class CortexTurn(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One turn of a córtex conversation (``user`` or ``cortex``).

    Immutable (no soft-delete): turns are append-only. ``owner_user_id`` is
    duplicated from the parent conversation on purpose so the isolation filter
    is a column predicate, never a join (defence in depth on BYPASSRLS).
    """

    __tablename__ = "cortex_turns"
    __table_args__ = (
        Index("ix_cortex_turns_conversation", "conversation_id", "created_at"),
        Index("ix_cortex_turns_owner", "owner_user_id"),
        CheckConstraint(
            "role IN ('user', 'cortex')",
            name="ck_cortex_turns_role",
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cortex_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Redundant to the parent's owner on purpose (no-join isolation filter).
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # The model that produced the answer; NULL on ``user`` turns.
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Tool names invoked during the turn.
    tools_called: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # The effective reasoning effort of the turn (audit).
    reasoning_effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Free-form: degraded/sdk flags, recall_hits, etc.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"CortexTurn(id={self.id!r}, conversation_id={self.conversation_id!r},"
            f" role={self.role!r})"
        )


__all__ = ["CortexConversation", "CortexTurn"]
