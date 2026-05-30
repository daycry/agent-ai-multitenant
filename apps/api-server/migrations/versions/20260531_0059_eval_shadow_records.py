"""eval_shadow_records — the Plan 14 task_14_09 shadow-eval recording table + RLS.

A shadow eval (Plan 14 task_14_09) replays a configurable random SAMPLE (5%
default) of real, COMPLETED tasks through a specialised reviewer agent / the
judge to RECORD a quality signal — it NEVER blocks or alters the real
execution (Plan 14 *Decisiones Clave*: "Shadow evals NO bloquean ejecución
real, solo registran resultado"). This single tenant-owned table is that
recording; it mirrors :class:`api_server.db.evals.EvalShadowRecord` 1:1:

  * ``source_task_id`` / ``source_execution_id`` — provenance to the REAL
    task/execution that was sampled. Both ``SET NULL`` on delete (the shadow
    record outlives the real rows) and the table is write-only against them —
    the real task is never updated through the shadow path.
  * ``shadow_run_id`` — the ``eval_runs`` row (against a ``shadow``-kind
    dataset) the judge produced for the replica; ``SET NULL`` on delete.
  * ``status`` / ``verdict`` — the lifecycle (``sampled`` -> ``judged`` /
    ``error``) and the replica's pass/fail/error once judged.
  * ``sample_rate`` — the operator-configurable sampling fraction in effect
    when the task was picked (provenance/audit), a fraction in [0, 1].

Tenancy decision (CLAUDE.md principle 1): tenant-owned — ``tenant_id`` NOT NULL
+ the canonical FOR ALL tenant-isolation RLS policy (the NULLIF + ``::uuid``
cast shape copied verbatim from 0058_eval_tables), so a tenant's shadow records
are visible only to that tenant.

Single head before this migration is ``0058_eval_tables``; this is
``0059_eval_shadow_records``. Fully reversible: ``downgrade`` drops the policy,
disables RLS, then drops the table. Proven by an up / down to
``0040_sso_email_domains`` / up cycle.

Revision ID: 0059_eval_shadow_records
Revises: 0058_eval_tables
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0059_eval_shadow_records"
down_revision: str | Sequence[str] | None = "0058_eval_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# 0058_eval_tables pattern). NULLIF(..., '') turns an unset GUC's empty string
# into NULL before the ::uuid cast, so an unset session matches zero rows (safe
# default). FORCE so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
def _rls_up(table: str) -> tuple[str, ...]:
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    )


def _rls_down(table: str) -> tuple[str, ...]:
    return (
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    )


def _ts_col(name: str, *, nullable: bool, default_now: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name,
        postgresql.TIMESTAMP(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "eval_shadow_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("shadow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'sampled'")
        ),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column(
            "sample_rate",
            sa.Numeric(precision=6, scale=5),
            nullable=False,
            server_default=sa.text("0.05"),
        ),
        _ts_col("created_at", nullable=False, default_now=True),
        _ts_col("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_shadow_records"),
        sa.ForeignKeyConstraint(
            ["source_task_id"],
            ["tasks.id"],
            name="fk_eval_shadow_records_source_task",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_execution_id"],
            ["executions.id"],
            name="fk_eval_shadow_records_source_execution",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["shadow_run_id"],
            ["eval_runs.id"],
            name="fk_eval_shadow_records_shadow_run",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "sample_rate >= 0 AND sample_rate <= 1",
            name="ck_eval_shadow_records_sample_rate_unit_range",
        ),
    )
    op.create_index("ix_eval_shadow_records_tenant_id", "eval_shadow_records", ["tenant_id"])
    op.create_index(
        "ix_eval_shadow_records_tenant_status",
        "eval_shadow_records",
        ["tenant_id", "status"],
    )
    op.create_index("ix_eval_shadow_records_source_task", "eval_shadow_records", ["source_task_id"])
    op.create_index("ix_eval_shadow_records_shadow_run", "eval_shadow_records", ["shadow_run_id"])

    for stmt in _rls_up("eval_shadow_records"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _rls_down("eval_shadow_records"):
        op.execute(stmt)

    op.drop_index("ix_eval_shadow_records_shadow_run", table_name="eval_shadow_records")
    op.drop_index("ix_eval_shadow_records_source_task", table_name="eval_shadow_records")
    op.drop_index("ix_eval_shadow_records_tenant_status", table_name="eval_shadow_records")
    op.drop_index("ix_eval_shadow_records_tenant_id", table_name="eval_shadow_records")
    op.drop_table("eval_shadow_records")
