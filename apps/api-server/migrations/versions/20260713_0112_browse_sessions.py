"""Sesiones de navegador del córtex con aprobación humana (ADR 0080).

Crea ``browse_sessions``: el córtex no navega, PIDE navegar; el owner ve el
guion exacto (URLs, clicks, lo que se teclea) y aprueba o rechaza. Solo tras la
aprobación el worker lanza el `browser-runtime` efímero.

``tenant_id`` nullable (el córtex es plataforma, como ``llm_usage_events``).
Reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0112_browse_sessions"
down_revision: str | Sequence[str] | None = "0111_marketplace_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "browse_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_approval"),
        sa.Column("goal", sa.String(500), nullable=False),
        sa.Column("steps", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("budgets", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("decided_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending_approval', 'approved', 'running', 'done', 'failed', 'rejected')",
            name="ck_browse_sessions_status",
        ),
    )
    # El inbox del owner: lo pendiente de decidir, por antigüedad.
    op.create_index(
        "ix_browse_sessions_owner_status",
        "browse_sessions",
        ["owner_user_id", "status", "created_at"],
    )
    op.execute("ALTER TABLE browse_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE browse_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY browse_sessions_tenant_isolation"
        " ON browse_sessions FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS browse_sessions_tenant_isolation ON browse_sessions")
    op.execute("ALTER TABLE browse_sessions DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_browse_sessions_owner_status", table_name="browse_sessions")
    op.drop_table("browse_sessions")
