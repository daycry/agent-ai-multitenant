"""platform_settings table — global platform configuration (task_02_13).

Per spec §7.9: a handful of settings are platform-wide hard limits, not
tenant knobs. `max_review_retries` is the first — a tenant cannot loosen
it; only the System Admin may change it.

The table is deliberately NOT tenant-scoped and carries no RLS: it is
global. Write access is gated in application code (db/platform_settings.py
checks the actor is a System Admin).

Revision ID: 0011_platform_settings
Revises: 0010_executions
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_platform_settings"
down_revision: str | Sequence[str] | None = "0010_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=80), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
