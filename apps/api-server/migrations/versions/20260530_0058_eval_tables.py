"""eval_* — the Plan 14 Fase A quality-eval data foundation + RLS.

Plan 14 task_14_02 (the migration task_14_01 deferred). Five tenant-owned tables
back continuous-quality evals (LLM-as-judge in Fase B, CI/shadow in Fase C,
dashboards in Fase D all build against this contract). They mirror the ORM
shapes in :mod:`api_server.db.evals` 1:1:

  * ``eval_datasets`` — a per-tenant *golden dataset* (Plan 14 Decisiones Clave:
    "Golden dataset por tenant"): the curated benchmark, the agent/role it
    targets, and (via its children) the criteria a judge scores against.
  * ``eval_dataset_items`` — one golden row inside a dataset: the input the
    subject is run against, the reference/expected output, and provenance
    (``source_task_id`` / ``source_execution_id``) back to the real APPROVED
    task/execution it was promoted from (task_14_02). The partial-UNIQUE on
    ``(dataset_id, source_task_id)`` is what makes promotion IDEMPOTENT — a
    second promote of the same task into the same dataset collides instead of
    duplicating.
  * ``eval_criteria`` — a judging criterion (rubric / weight / pass threshold)
    belonging to a dataset; drives LLM-as-judge in Fase B.
  * ``eval_runs`` — one execution of a dataset against a subject (a prompt
    version / agent): status, subject ref, denormalised aggregate metrics.
  * ``eval_results`` — the per-item outcome within a run: produced output,
    per-criterion scores (JSONB), verdict, per-item usage.

Tenancy decision (CLAUDE.md principle 1): every table is tenant-owned —
``tenant_id`` NOT NULL + the canonical FOR ALL tenant-isolation RLS policy
(the NULLIF + ``::uuid`` cast shape copied verbatim from 0055_incoming_webhooks),
so a tenant's golden dataset AND its criteria are visible only to that tenant.

Single head before this migration is ``0057_webhook_event_replay``; this is
``0058_eval_tables``. Fully reversible: ``downgrade`` drops each policy, disables
RLS, then drops the tables children-first (results -> runs/items/criteria ->
datasets). Proven by an up / down to ``0040_sso_email_domains`` / up cycle.

Revision ID: 0058_eval_tables
Revises: 0057_webhook_event_replay
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_eval_tables"
down_revision: str | Sequence[str] | None = "0057_webhook_event_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# 0055_incoming_webhooks pattern). NULLIF(..., '') turns an unset GUC's empty
# string into NULL before the ::uuid cast, so an unset session matches zero
# rows (safe default). FORCE so the policy applies even to the table owner.
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
    # --- eval_datasets ------------------------------------------------------
    op.create_table(
        "eval_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default=sa.text("'golden'")),
        sa.Column("target_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_role", sa.String(length=32), nullable=True),
        _ts_col("created_at", nullable=False, default_now=True),
        _ts_col("updated_at", nullable=False, default_now=True),
        _ts_col("deleted_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_datasets"),
        sa.ForeignKeyConstraint(
            ["target_agent_id"],
            ["agents.id"],
            name="fk_eval_datasets_target_agent",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_eval_datasets_tenant_id", "eval_datasets", ["tenant_id"])
    op.create_index(
        "ix_eval_datasets_tenant_kind",
        "eval_datasets",
        ["tenant_id", "kind"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_eval_datasets_target_agent", "eval_datasets", ["target_agent_id"])

    # --- eval_dataset_items -------------------------------------------------
    op.create_table(
        "eval_dataset_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column(
            "reference_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        _ts_col("created_at", nullable=False, default_now=True),
        _ts_col("updated_at", nullable=False, default_now=True),
        _ts_col("deleted_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_dataset_items"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["eval_datasets.id"],
            name="fk_eval_dataset_items_dataset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_task_id"],
            ["tasks.id"],
            name="fk_eval_dataset_items_source_task",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_execution_id"],
            ["executions.id"],
            name="fk_eval_dataset_items_source_execution",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_eval_dataset_items_tenant_id", "eval_dataset_items", ["tenant_id"])
    op.create_index("ix_eval_dataset_items_dataset", "eval_dataset_items", ["dataset_id"])
    op.create_index("ix_eval_dataset_items_tenant", "eval_dataset_items", ["tenant_id"])
    op.create_index("ix_eval_dataset_items_source_task", "eval_dataset_items", ["source_task_id"])
    # Idempotent promotion: a second promote of the same real task into the
    # same dataset collides on this partial UNIQUE instead of duplicating
    # (task_14_02). Partial so items NOT promoted from a task (source NULL,
    # e.g. a hand-authored golden) never collide.
    op.create_index(
        "uq_eval_dataset_items_source_task",
        "eval_dataset_items",
        ["dataset_id", "source_task_id"],
        unique=True,
        postgresql_where=sa.text("source_task_id IS NOT NULL AND deleted_at IS NULL"),
    )

    # --- eval_criteria ------------------------------------------------------
    op.create_table(
        "eval_criteria",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("judge_instruction", sa.Text(), nullable=False),
        sa.Column(
            "weight",
            sa.Numeric(precision=6, scale=3),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "pass_threshold",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
            server_default=sa.text("0.5"),
        ),
        _ts_col("created_at", nullable=False, default_now=True),
        _ts_col("updated_at", nullable=False, default_now=True),
        _ts_col("deleted_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_criteria"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["eval_datasets.id"],
            name="fk_eval_criteria_dataset",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("weight >= 0", name="ck_eval_criteria_weight_non_negative"),
        sa.CheckConstraint(
            "pass_threshold >= 0 AND pass_threshold <= 1",
            name="ck_eval_criteria_pass_threshold_unit_range",
        ),
    )
    op.create_index("ix_eval_criteria_tenant_id", "eval_criteria", ["tenant_id"])
    op.create_index("ix_eval_criteria_dataset", "eval_criteria", ["dataset_id"])
    op.create_index("ix_eval_criteria_tenant", "eval_criteria", ["tenant_id"])

    # --- eval_runs ----------------------------------------------------------
    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("subject_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_prompt_version", sa.String(length=64), nullable=True),
        sa.Column("judge_model", sa.String(length=120), nullable=True),
        _ts_col("started_at", nullable=True),
        _ts_col("finished_at", nullable=True),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("passed_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pass_rate", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("mean_latency_ms", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("mean_tokens", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("mean_cost_usd", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column(
            "aggregate_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _ts_col("created_at", nullable=False, default_now=True),
        _ts_col("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_runs"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["eval_datasets.id"],
            name="fk_eval_runs_dataset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_agent_id"],
            ["agents.id"],
            name="fk_eval_runs_subject_agent",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("total_items >= 0", name="ck_eval_runs_total_items_non_negative"),
        sa.CheckConstraint("passed_items >= 0", name="ck_eval_runs_passed_items_non_negative"),
        sa.CheckConstraint(
            "pass_rate IS NULL OR (pass_rate >= 0 AND pass_rate <= 1)",
            name="ck_eval_runs_pass_rate_unit_range",
        ),
    )
    op.create_index("ix_eval_runs_tenant_id", "eval_runs", ["tenant_id"])
    op.create_index("ix_eval_runs_dataset", "eval_runs", ["dataset_id"])
    op.create_index("ix_eval_runs_tenant_status", "eval_runs", ["tenant_id", "status"])
    op.create_index("ix_eval_runs_subject_agent", "eval_runs", ["subject_agent_id"])

    # --- eval_results -------------------------------------------------------
    op.create_table(
        "eval_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("produced_output", sa.Text(), nullable=True),
        sa.Column(
            "criterion_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "verdict", sa.String(length=16), nullable=False, server_default=sa.text("'fail'")
        ),
        sa.Column("overall_score", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=14, scale=6), nullable=True),
        _ts_col("created_at", nullable=False, default_now=True),
        _ts_col("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_results"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["eval_runs.id"],
            name="fk_eval_results_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["eval_dataset_items.id"],
            name="fk_eval_results_item",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_eval_results_latency_non_negative",
        ),
        sa.CheckConstraint(
            "tokens IS NULL OR tokens >= 0", name="ck_eval_results_tokens_non_negative"
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0", name="ck_eval_results_cost_non_negative"
        ),
    )
    op.create_index("ix_eval_results_tenant_id", "eval_results", ["tenant_id"])
    op.create_index("ix_eval_results_run", "eval_results", ["run_id"])
    op.create_index("ix_eval_results_item", "eval_results", ["item_id"])
    op.create_index("ix_eval_results_tenant_verdict", "eval_results", ["tenant_id", "verdict"])

    for table in (
        "eval_datasets",
        "eval_dataset_items",
        "eval_criteria",
        "eval_runs",
        "eval_results",
    ):
        for stmt in _rls_up(table):
            op.execute(stmt)


def downgrade() -> None:
    for table in (
        "eval_results",
        "eval_runs",
        "eval_criteria",
        "eval_dataset_items",
        "eval_datasets",
    ):
        for stmt in _rls_down(table):
            op.execute(stmt)

    # Children first (FKs point inward to datasets).
    op.drop_index("ix_eval_results_tenant_verdict", table_name="eval_results")
    op.drop_index("ix_eval_results_item", table_name="eval_results")
    op.drop_index("ix_eval_results_run", table_name="eval_results")
    op.drop_index("ix_eval_results_tenant_id", table_name="eval_results")
    op.drop_table("eval_results")

    op.drop_index("ix_eval_runs_subject_agent", table_name="eval_runs")
    op.drop_index("ix_eval_runs_tenant_status", table_name="eval_runs")
    op.drop_index("ix_eval_runs_dataset", table_name="eval_runs")
    op.drop_index("ix_eval_runs_tenant_id", table_name="eval_runs")
    op.drop_table("eval_runs")

    op.drop_index("ix_eval_criteria_tenant", table_name="eval_criteria")
    op.drop_index("ix_eval_criteria_dataset", table_name="eval_criteria")
    op.drop_index("ix_eval_criteria_tenant_id", table_name="eval_criteria")
    op.drop_table("eval_criteria")

    op.drop_index("uq_eval_dataset_items_source_task", table_name="eval_dataset_items")
    op.drop_index("ix_eval_dataset_items_source_task", table_name="eval_dataset_items")
    op.drop_index("ix_eval_dataset_items_tenant", table_name="eval_dataset_items")
    op.drop_index("ix_eval_dataset_items_dataset", table_name="eval_dataset_items")
    op.drop_index("ix_eval_dataset_items_tenant_id", table_name="eval_dataset_items")
    op.drop_table("eval_dataset_items")

    op.drop_index("ix_eval_datasets_target_agent", table_name="eval_datasets")
    op.drop_index("ix_eval_datasets_tenant_kind", table_name="eval_datasets")
    op.drop_index("ix_eval_datasets_tenant_id", table_name="eval_datasets")
    op.drop_table("eval_datasets")
