"""Eval ORM models (Plan 14 task_14_01) — the quality-eval data foundation.

Five tables make up the continuous-quality substrate. They are the
DATA shape only; LLM-as-judge scoring (Plan 14 Fase B), CI/shadow evals
(Fase C) and the dashboards (Fase D) build against this contract. The
migration that creates the tables, indexes, FKs and RLS policies is a
later task (task_14_03 or a dedicated one); this module ships only the
ORM declarations + enums.

  - **`eval_datasets`** — a per-tenant *golden dataset*: a curated set of
    real (approved) tasks promoted into a reusable benchmark, with the
    agent/role it targets and the criteria a judge scores against.
    **Tenant-owned**: ``tenant_id NOT NULL`` + RLS — a tenant's golden
    dataset (its data AND its criteria) is visible only to that tenant
    (Plan 14 *Decisiones Clave*: "Golden dataset por tenant").

  - **`eval_dataset_items`** — one row per golden item inside a dataset:
    the input prompt the subject is run against, the reference/expected
    output, and provenance back to the real task/execution it was
    promoted from. **Tenant-owned** via ``tenant_id`` + RLS; cascades
    from its dataset.

  - **`eval_criteria`** — a judging criterion belonging to a dataset:
    name, description, the judge instruction/rubric, a weight and a pass
    threshold. Drives LLM-as-judge in Fase B. **Tenant-owned** + RLS;
    cascades from its dataset.

  - **`eval_runs`** — one execution of a dataset against a *subject*
    (e.g. a prompt version / agent): status, start/finish timestamps,
    the subject reference (agent + prompt version), and denormalised
    aggregate metrics (pass rate, mean cost/latency/tokens). **Tenant-
    owned** + RLS.

  - **`eval_results`** — the per-dataset-item outcome within a run: the
    item evaluated, the produced output, the per-criterion scores
    (JSONB: ``[{criterion_id, score, passed, rationale}, ...]``), an
    overall pass/fail verdict, and per-item latency/tokens/cost.
    **Tenant-owned** + RLS; cascades from its run.

All tenant-owned tables use the same UUID v7 + timestamp mixins and the
RLS isolation guarantee as the rest of the domain.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# Enums (StrEnum so values persist as stable TEXT)
# =============================================================================
class EvalDatasetKind(enum.StrEnum):
    """What a golden dataset benchmarks.

    - ``golden``: curated real, approved tasks promoted into a benchmark
      (the default; Plan 14 task_14_02 promotion target).
    - ``regression``: a frozen set guarding against quality regressions
      in CI (Fase C).
    - ``shadow``: items collected from the production shadow-eval sample
      (Fase C, 5% default).
    """

    GOLDEN = "golden"
    REGRESSION = "regression"
    SHADOW = "shadow"


class EvalRunStatus(enum.StrEnum):
    """Lifecycle of an eval run against a subject.

    A freshly created run lands in ``pending``; the eval worker flips it
    to ``running`` while it evaluates each item, then to ``completed`` on
    success or ``failed`` on error. ``cancelled`` is the manual stop.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvalResultVerdict(enum.StrEnum):
    """Overall pass/fail of a single item within a run.

    ``error`` is the escape hatch when the subject (or the judge) failed
    to produce a scorable output — distinct from a genuine ``fail``.
    """

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


# =============================================================================
# eval_datasets — a per-tenant golden dataset
# =============================================================================
class EvalDataset(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A per-tenant golden dataset (Plan 14 *Decisiones Clave*).

    Tenant-owned: ``tenant_id NOT NULL`` (via :class:`TenantScopedMixin`)
    + RLS. A tenant's dataset, its items and its criteria are visible
    only to that tenant.
    """

    __tablename__ = "eval_datasets"
    __table_args__ = (
        Index(
            "ix_eval_datasets_tenant_kind",
            "tenant_id",
            "kind",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_eval_datasets_target_agent", "target_agent_id"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'golden'"))

    # The agent/role this dataset targets. The agent FK is nullable and
    # SET NULL on delete (a dataset outlives the specific agent template);
    # ``target_role`` is the durable, agent-independent target label
    # (one of domain.AgentRole) so the dataset stays meaningful.
    target_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_role: Mapped[str | None] = mapped_column(String(32), nullable=True)

    criteria: Mapped[list[EvalCriterion]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    items: Mapped[list[EvalDatasetItem]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    runs: Mapped[list[EvalRun]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"EvalDataset(id={self.id!r}, name={self.name!r}, kind={self.kind!r})"


# =============================================================================
# eval_dataset_items — one golden item (input + reference) in a dataset
# =============================================================================
class EvalDatasetItem(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """A single golden row: the input the subject is run against plus the
    reference/expected output.

    Promotion (task_14_02) copies these idempotently from a real,
    APPROVED task/execution; ``source_task_id`` / ``source_execution_id``
    keep provenance so a re-promote can dedupe.
    """

    __tablename__ = "eval_dataset_items"
    __table_args__ = (
        Index("ix_eval_dataset_items_dataset", "dataset_id"),
        Index("ix_eval_dataset_items_tenant", "tenant_id"),
        Index("ix_eval_dataset_items_source_task", "source_task_id"),
        # Idempotent promotion (task_14_02): a second promote of the SAME real
        # task into the SAME dataset collides on this partial UNIQUE instead of
        # duplicating. Partial so hand-authored items (source_task_id NULL) and
        # soft-deleted rows never collide.
        Index(
            "uq_eval_dataset_items_source_task",
            "dataset_id",
            "source_task_id",
            unique=True,
            postgresql_where=text("source_task_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    dataset_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_datasets.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The golden input the subject is run against (prompt + structured
    # inputs). JSONB so the shape evolves migration-free.
    input: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # The reference / expected output the judge compares against. NULL for
    # criteria-only (rubric) datasets where no single golden answer exists.
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Provenance: the real task/execution this item was promoted from.
    # SET NULL on delete — the golden item survives the real task it came
    # from. Used by task_14_02 to promote idempotently (skip if present).
    source_task_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_execution_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("executions.id", ondelete="SET NULL"),
        nullable=True,
    )

    dataset: Mapped[EvalDataset] = relationship(back_populates="items")
    results: Mapped[list[EvalResult]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"EvalDatasetItem(id={self.id!r}, dataset={self.dataset_id!r})"


# =============================================================================
# eval_criteria — a judging criterion belonging to a dataset
# =============================================================================
class EvalCriterion(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A judging criterion (name, rubric, weight, pass threshold).

    Drives LLM-as-judge in Fase B: the judge model scores each item
    against this criterion's ``judge_instruction`` rubric, producing a
    score in [0, 1]; ``pass_threshold`` decides pass/fail and ``weight``
    sets its contribution to the item's overall verdict.
    """

    __tablename__ = "eval_criteria"
    __table_args__ = (
        Index("ix_eval_criteria_dataset", "dataset_id"),
        Index("ix_eval_criteria_tenant", "tenant_id"),
        CheckConstraint("weight >= 0", name="ck_eval_criteria_weight_non_negative"),
        CheckConstraint(
            "pass_threshold >= 0 AND pass_threshold <= 1",
            name="ck_eval_criteria_pass_threshold_unit_range",
        ),
    )

    dataset_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_datasets.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The instruction / rubric handed to the judge model (e.g. "Does the
    # code follow PEP 8?", "Is the brand tone respected?").
    judge_instruction: Mapped[str] = mapped_column(Text, nullable=False)

    # Relative contribution to the item's overall verdict. Numeric(6,3)
    # maps to Decimal so no float rounding on weighted aggregates.
    weight: Mapped[Decimal] = mapped_column(
        Numeric(precision=6, scale=3), nullable=False, server_default=text("1")
    )
    # Minimum normalised score in [0, 1] for this criterion to count as
    # passed. Default 0.5 — overridable per criterion.
    pass_threshold: Mapped[Decimal] = mapped_column(
        Numeric(precision=4, scale=3), nullable=False, server_default=text("0.5")
    )

    dataset: Mapped[EvalDataset] = relationship(back_populates="criteria")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"EvalCriterion(id={self.id!r}, name={self.name!r})"


# =============================================================================
# eval_runs — one execution of a dataset against a subject
# =============================================================================
class EvalRun(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """One run of a dataset against a *subject* (a prompt version / agent).

    Runs are NOT soft-deleted — they are an immutable record of a quality
    measurement, like :class:`api_server.db.domain.Execution`. The
    ``aggregate_metrics`` JSONB + the denormalised ``pass_rate`` /
    ``mean_*`` columns are roll-ups the dashboards read without scanning
    ``eval_results``.
    """

    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("ix_eval_runs_dataset", "dataset_id"),
        Index("ix_eval_runs_tenant_status", "tenant_id", "status"),
        Index("ix_eval_runs_subject_agent", "subject_agent_id"),
        CheckConstraint("total_items >= 0", name="ck_eval_runs_total_items_non_negative"),
        CheckConstraint("passed_items >= 0", name="ck_eval_runs_passed_items_non_negative"),
        CheckConstraint(
            "pass_rate IS NULL OR (pass_rate >= 0 AND pass_rate <= 1)",
            name="ck_eval_runs_pass_rate_unit_range",
        ),
    )

    dataset_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_datasets.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )

    # --- Subject under evaluation (the thing being graded) ----------------
    # The agent and the specific prompt version this run evaluated. The
    # agent FK SET NULLs on delete (the run's measurement survives the
    # agent template); ``subject_prompt_version`` is the durable label.
    subject_agent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    subject_prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The judge model used (different from the subject's model — avoids
    # bias; Plan 14 *Decisiones Clave*). NULL until Fase B wires it.
    judge_model: Mapped[str | None] = mapped_column(String(120), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # --- Denormalised aggregate metrics (roll-ups of eval_results) --------
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    passed_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Fraction in [0, 1]. NULL until the run completes.
    pass_rate: Mapped[Decimal | None] = mapped_column(Numeric(precision=4, scale=3), nullable=True)
    mean_latency_ms: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=12, scale=2), nullable=True
    )
    mean_tokens: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=2), nullable=True
    )
    mean_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=14, scale=6), nullable=True
    )
    # Open-ended extra metrics (p50/p95 latency, per-criterion breakdowns
    # computed in Fase B). JSONB so the shape evolves migration-free.
    aggregate_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    dataset: Mapped[EvalDataset] = relationship(back_populates="runs")
    results: Mapped[list[EvalResult]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"EvalRun(id={self.id!r}, dataset={self.dataset_id!r}, status={self.status!r})"


# =============================================================================
# eval_results — per-item outcome within a run
# =============================================================================
class EvalResult(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """The outcome of evaluating one dataset item within one run.

    Results are NOT soft-deleted — immutable measurement records. The
    ``criterion_scores`` JSONB holds the per-criterion shape Fase B
    produces::

        [
          {"criterion_id": "<uuid>", "score": 0.92, "passed": true,
           "rationale": "Follows PEP 8; no lint errors."},
          ...
        ]

    ``verdict`` is the item's overall pass/fail/error; the latency/tokens/
    cost columns are this item's own usage (the run's ``mean_*`` are the
    roll-ups across results).
    """

    __tablename__ = "eval_results"
    __table_args__ = (
        Index("ix_eval_results_run", "run_id"),
        Index("ix_eval_results_item", "item_id"),
        Index("ix_eval_results_tenant_verdict", "tenant_id", "verdict"),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_eval_results_latency_non_negative",
        ),
        CheckConstraint(
            "tokens IS NULL OR tokens >= 0", name="ck_eval_results_tokens_non_negative"
        ),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0", name="ck_eval_results_cost_non_negative"
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The item evaluated. SET NULL on delete so the result survives if the
    # golden item is later removed from the dataset.
    item_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("eval_dataset_items.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The output the subject produced for this item.
    produced_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-criterion scores — see the class docstring for the shape.
    criterion_scores: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'fail'"))
    # Weighted overall score in [0, 1] across the dataset's criteria.
    overall_score: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=4, scale=3), nullable=True
    )

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(precision=14, scale=6), nullable=True)

    run: Mapped[EvalRun] = relationship(back_populates="results")
    item: Mapped[EvalDatasetItem | None] = relationship(back_populates="results")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"EvalResult(id={self.id!r}, run={self.run_id!r}, verdict={self.verdict!r})"


__all__ = [
    "EvalCriterion",
    "EvalDataset",
    "EvalDatasetItem",
    "EvalDatasetKind",
    "EvalResult",
    "EvalResultVerdict",
    "EvalRun",
    "EvalRunStatus",
]
