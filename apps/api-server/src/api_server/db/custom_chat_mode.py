"""Tenant-defined custom chat modes (Plan 03 task_03_08).

A tenant can extend the chat-mode catalog beyond the three built-ins
(planning / discussion / execution) with arbitrary modes — e.g.
"design-review", "post-mortem", "sprint-grooming" — each with its
own system prompt and tool whitelist. The conversation references a
custom mode by name (`Conversation.custom_mode_name`); this table
defines what the name means for the tenant.

RLS isolates rows per tenant. The bridge from the ORM row to the
in-memory `CustomModeSpec` (see `api_server.chat.modes`) is
`row_to_spec` below.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.chat.modes import CustomModeSpec
from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class CustomChatMode(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """One tenant-defined chat mode.

    `name` is the stable identifier the conversation references; it
    cannot collide with another live custom mode in the same tenant.
    Labels are bilingual (ES + EN — the only two languages this
    version supports per CLAUDE.md §13).
    """

    __tablename__ = "custom_chat_modes"
    __table_args__ = (
        Index(
            "ix_custom_chat_modes_tenant_name",
            "tenant_id",
            "name",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_custom_chat_modes_tenant_name",
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    label_es: Mapped[str] = mapped_column(String(120), nullable=False)
    label_en: Mapped[str] = mapped_column(String(120), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)

    # JSONB array of tool names; defaults to []. The worker forwards this
    # whitelist to the agent-runtime task spec; the runtime's ToolRegistry
    # enforces it at tool-call time — a tool outside the set is rejected
    # before it runs (task_06_14_07). The full layered guardrail engine
    # (pre_llm / post_llm / pre_tool / post_tool) lands in Plan 11.
    allowed_tools: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    planning_subgraph: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # The user who last edited this mode — kept for audit, NOT for
    # access control (RLS already does that).
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


def row_to_spec(row: CustomChatMode) -> CustomModeSpec:
    """Adapter from the ORM row to the in-memory spec the resolver uses."""
    return CustomModeSpec(
        name=row.name,
        label_es=row.label_es,
        label_en=row.label_en,
        system_prompt=row.system_prompt,
        allowed_tools=tuple(row.allowed_tools),
        planning_subgraph=row.planning_subgraph,
    )


__all__ = [
    "CustomChatMode",
    "row_to_spec",
]
