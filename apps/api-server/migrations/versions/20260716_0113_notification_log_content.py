"""Contenido del mensaje en notification_logs + receipts de plataforma (AUD16-10/11).

Una notificación in-app solo decía QUE pasó algo (event_type), no QUÉ pasó:
el body renderizado se descartaba tras la entrega. ``subject``/``body``
(truncados por el writer: 200/2000 chars) viajan ahora en la fila para que el
inbox muestre el contenido. Nullable — las filas históricas quedan NULL y los
canales externos (telegram/email/…) pueden seguir sin persistirlo.

Además, ``notification_log_reads.tenant_id`` pasa a NULLABLE: el inbox de
PLATAFORMA del System Admin (AUD16-10) marca leídos envíos ``tenant_id IS
NULL`` y su receipt es igualmente platform-scoped — el espejo exacto de
``notification_logs.tenant_id`` (ya nullable desde task_10_02).

Reversible (el downgrade borra los receipts de plataforma antes de restaurar
NOT NULL — son solo marcadores de lectura).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0113_notification_log_content"
down_revision: str | Sequence[str] | None = "0112_browse_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_logs",
        sa.Column("subject", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("body", sa.String(length=2000), nullable=True),
    )
    op.alter_column(
        "notification_log_reads",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM notification_log_reads WHERE tenant_id IS NULL")
    op.alter_column(
        "notification_log_reads",
        "tenant_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("notification_logs", "body")
    op.drop_column("notification_logs", "subject")
