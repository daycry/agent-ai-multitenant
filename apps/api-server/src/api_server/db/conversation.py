"""Conversation and Message models — Plan 03 task_03_01.

A `Conversation` lives inside a project and groups the chat history a
user has with their agent team. Conversation has a `current_mode`
(Planning / Discusión / Ejecución / Custom — spec §8.2) so the same
single conversation switches mode without losing context (one of the
key product decisions of Plan 03).

A `Message` is one entry in the feed. Three author kinds:

- ``user``    — a human typed it.
- ``agent``   — an agent in the team wrote it.
- ``system``  — the platform itself wrote it (mode-change banner, plan
                generated, agent joined/left, etc.).

Each message captures the mode in effect *at send time*: when the
operator switches modes, prior messages keep their original mode so the
historical context is faithful. Attachments and a soft-FK to a Plan are
both per-message (a "Plan generated" system message references the
plan; a "Estimación adjunta" user message can carry a file).
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
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
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ChatMode(enum.StrEnum):
    """Built-in chat modes (spec §8.2). ``custom`` is the escape hatch for
    tenant-defined modes; the human-readable label lives in
    ``Conversation.custom_mode_name``."""

    PLANNING = "planning"
    DISCUSSION = "discussion"
    EXECUTION = "execution"
    CUSTOM = "custom"


class MessageAuthorKind(enum.StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


# =============================================================================
# Conversation
# =============================================================================
class Conversation(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "ix_conversations_tenant_project",
            "tenant_id",
            "project_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_conversations_related_plan", "related_plan_id"),
        # Custom mode requires a name; built-in modes leave the name NULL.
        CheckConstraint(
            "(current_mode = 'custom' AND custom_mode_name IS NOT NULL)"
            " OR (current_mode <> 'custom' AND custom_mode_name IS NULL)",
            name="ck_conversations_custom_mode_name_consistency",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Built-in mode or 'custom' (label lives in custom_mode_name). 32 chars
    # is wide enough for any built-in plus a few characters of margin for
    # forward-compatible additions.
    current_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'planning'")
    )
    custom_mode_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Soft FK — promoted to a real FK in the migration once both tables
    # exist (avoids the chicken-and-egg between plans.conversation_id and
    # conversations.related_plan_id).
    related_plan_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"Conversation(id={self.id!r}, project_id={self.project_id!r},"
            f" current_mode={self.current_mode!r})"
        )


# =============================================================================
# Message
# =============================================================================
class Message(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One message in a conversation feed.

    Messages are **immutable** in product terms (edits become a new
    message + a system "edited" marker), but the table itself does not
    forbid UPDATE — the constraint lives in the API layer (task_03_03).
    No soft-delete: removing a message is rare and audit-noisy.

    ``mode`` captures the conversation mode in effect at send-time so a
    later mode switch does not retroactively rewrite history.
    """

    __tablename__ = "messages"
    __table_args__ = (
        # Feed ordering is (conversation_id, id) — UUID v7 is timestamp-
        # sortable so this index doubles as the chronological index.
        Index("ix_messages_conversation_id", "conversation_id", "id"),
        Index("ix_messages_tenant_id", "tenant_id"),
        Index(
            "ix_messages_author_agent",
            "author_agent_id",
            postgresql_where=text("author_agent_id IS NOT NULL"),
        ),
        # Author kind <-> author_*_id invariant.
        #   user   -> author_user_id  NOT NULL, author_agent_id NULL
        #   agent  -> author_agent_id NOT NULL, author_user_id  NULL
        #   system -> both NULL
        CheckConstraint(
            "(author_kind = 'user'   AND author_user_id  IS NOT NULL"
            "                        AND author_agent_id IS NULL)"
            " OR (author_kind = 'agent' AND author_agent_id IS NOT NULL"
            "                            AND author_user_id  IS NULL)"
            " OR (author_kind = 'system' AND author_user_id IS NULL"
            "                             AND author_agent_id IS NULL)",
            name="ck_messages_author_kind_consistency",
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )

    author_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    author_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )

    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    # Mode in effect at send time. Same 32-char width as Conversation.current_mode.
    mode: Mapped[str] = mapped_column(String(32), nullable=False)

    # Attachments — list of {kind, ref, name, size_bytes, mime_type, ...}.
    # JSONB rather than a child table: attachments are small metadata
    # blobs (the binary lives in MinIO; only the ref+name+size live here).
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Soft-FK to a plan the message references (e.g. system message
    # "Plan generated" or user message "I refined the plan"). NULL for
    # most messages.
    related_plan_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # Compression rollup (Plan 03 task_03_04): when a window of old
    # messages is summarised, the summary message has summary=true and
    # carries `summary_replaces` (list of message UUIDs) in its
    # attachments. Kept here as a flag-only column so the feed UI can
    # render summaries differently without parsing JSONB.
    is_summary: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"Message(id={self.id!r}, conversation_id={self.conversation_id!r},"
            f" author_kind={self.author_kind!r}, mode={self.mode!r})"
        )


__all__ = [
    "ChatMode",
    "Conversation",
    "Message",
    "MessageAuthorKind",
]
