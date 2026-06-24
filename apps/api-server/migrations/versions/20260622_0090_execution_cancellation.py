"""execution cancellation: cancel_requested_at + celery_task_id

Cooperative cancellation of a running execution (auditoría / task_prod06_cancel_01):
``cancel_requested_at`` is the operator's stop flag (the worker polls it to kill the
container and finalise the row as ``cancelled``); ``celery_task_id`` lets the cancel
endpoint ``revoke(terminate=True)`` the still-queued/running Celery job. Both nullable
and additive — existing rows keep working with NULL. Reversible.

Revision ID: 0090_execution_cancel
Revises: 0089_chat_model_config
Create Date: 2026-06-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_execution_cancel"
down_revision: str | Sequence[str] | None = "0089_chat_model_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("cancel_requested_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("celery_task_id", sa.String(length=155), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "celery_task_id")
    op.drop_column("executions", "cancel_requested_at")
