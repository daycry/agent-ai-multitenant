"""Consumo LLM de consumidores no-run (ADR 0116).

Crea ``llm_usage_events``: el asistente, el córtex y el planning consumían LLM
sin contabilizar (el gasto solo se derivaba de executions.total_cost_usd).
``tenant_id`` nullable (córtex = plataforma). Reversible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0109_llm_usage_events"
down_revision: str | Sequence[str] | None = "0108_assistant_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("provider_kind", sa.String(32), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("calls", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "source IN ('assistant', 'cortex', 'planning')",
            name="ck_llm_usage_events_source",
        ),
    )
    op.create_index(
        "ix_llm_usage_events_tenant_created", "llm_usage_events", ["tenant_id", "created_at"]
    )
    # RLS estándar: las filas de tenant solo las ve su tenant; las de plataforma
    # (tenant_id NULL, córtex) solo sesiones admin — deseado (ADR 0116).
    op.execute("ALTER TABLE llm_usage_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE llm_usage_events FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY llm_usage_events_tenant_isolation"
        " ON llm_usage_events FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS llm_usage_events_tenant_isolation ON llm_usage_events")
    op.execute("ALTER TABLE llm_usage_events DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_llm_usage_events_tenant_created", table_name="llm_usage_events")
    op.drop_table("llm_usage_events")
