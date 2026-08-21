"""Unit tests for the Eval ORM contract (Plan 14 task_14_01).

In-process only — no DB. We pin the column shape, enum values, defaults,
relationships, the per-criterion-score shape, and the tenant-scoping
decision the rest of Plan 14 depends on (golden dataset PER-TENANT). The
migration + RLS denial are exercised later (task_14_03 / a dedicated
migration test).

``domain`` is imported so the cross-module FK targets (agents, tasks,
executions) are registered with the mapper registry before we build eval
objects and walk their relationships.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

# Import for FK-target mapper registration (agents / tasks / executions).
from api_server.db import domain as _domain  # noqa: F401
from api_server.db.base import (
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from api_server.db.evals import (
    EvalCriterion,
    EvalDataset,
    EvalDatasetItem,
    EvalDatasetKind,
    EvalResult,
    EvalResultVerdict,
    EvalRun,
    EvalRunStatus,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Table names
# ---------------------------------------------------------------------------
EXPECTED_TABLES = {
    EvalDataset: "eval_datasets",
    EvalDatasetItem: "eval_dataset_items",
    EvalCriterion: "eval_criteria",
    EvalRun: "eval_runs",
    EvalResult: "eval_results",
}


@pytest.mark.parametrize("model,table", list(EXPECTED_TABLES.items()))
def test_model_has_expected_tablename(model: type, table: str) -> None:
    assert model.__tablename__ == table


# ---------------------------------------------------------------------------
# Enums — frozen value sets (renaming a value breaks persisted rows)
# ---------------------------------------------------------------------------
def test_dataset_kind_values() -> None:
    assert {k.value for k in EvalDatasetKind} == {"golden", "regression", "shadow"}


def test_run_status_values() -> None:
    assert {s.value for s in EvalRunStatus} == {
        "pending",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_result_verdict_values() -> None:
    assert {v.value for v in EvalResultVerdict} == {"pass", "fail", "error"}


def test_enums_are_string_valued() -> None:
    """StrEnum: the value persists as a plain string (TEXT column)."""
    assert EvalDatasetKind.GOLDEN == "golden"
    assert EvalRunStatus.RUNNING == "running"
    assert EvalResultVerdict.PASS == "pass"


# ---------------------------------------------------------------------------
# Tenant-scoping — every eval table is tenant-owned (NOT NULL + RLS-ready)
# ---------------------------------------------------------------------------
ALL_MODELS = [EvalDataset, EvalDatasetItem, EvalCriterion, EvalRun, EvalResult]


@pytest.mark.parametrize("model", ALL_MODELS)
def test_models_are_tenant_scoped(model: type) -> None:
    assert issubclass(model, TenantScopedMixin), f"{model.__name__} must be tenant-scoped for RLS"
    assert issubclass(model, UUIDPrimaryKeyMixin)
    assert issubclass(model, TimestampMixin)


@pytest.mark.cross_tenant
@pytest.mark.parametrize("model", ALL_MODELS)
def test_tenant_id_is_not_null(model: type) -> None:
    """Golden dataset is PER-TENANT (Plan 14 Decisiones Clave): the
    cross-tenant boundary is structurally present — tenant_id NOT NULL on
    every eval table so the RLS isolation policy (later migration) can
    attach. The DB-level denial test lives in the migration suite."""
    tenant_col = model.__table__.columns["tenant_id"]
    assert tenant_col.nullable is False, f"{model.__name__}.tenant_id must be NOT NULL"


@pytest.mark.parametrize("model", [EvalDataset, EvalDatasetItem, EvalCriterion])
def test_dataset_and_children_are_soft_deletable(model: type) -> None:
    assert issubclass(model, SoftDeleteMixin)


@pytest.mark.parametrize("model", [EvalRun, EvalResult])
def test_runs_and_results_are_immutable(model: type) -> None:
    """Runs/results are an immutable measurement record (like Execution)."""
    assert not issubclass(model, SoftDeleteMixin)


# ---------------------------------------------------------------------------
# EvalDataset — columns + target
# ---------------------------------------------------------------------------
def test_dataset_columns() -> None:
    cols = {c.name for c in EvalDataset.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "name",
        "description",
        "kind",
        "target_agent_id",
        "target_role",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_dataset_target_agent_fk_sets_null() -> None:
    fks = list(EvalDataset.__table__.columns["target_agent_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "agents.id"
    assert fks[0].ondelete == "SET NULL"


def test_dataset_construction_defaults() -> None:
    ds = EvalDataset(tenant_id=uuid4(), name="backend-dev golden")
    assert ds.name == "backend-dev golden"
    assert EvalDataset.__table__.columns["kind"].server_default is not None


# ---------------------------------------------------------------------------
# EvalDatasetItem — input + reference + provenance
# ---------------------------------------------------------------------------
def test_item_columns() -> None:
    cols = {c.name for c in EvalDatasetItem.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "dataset_id",
        "input",
        "expected_output",
        "reference_metadata",
        "source_task_id",
        "source_execution_id",
    } <= cols


def test_item_dataset_fk_cascades() -> None:
    fks = list(EvalDatasetItem.__table__.columns["dataset_id"].foreign_keys)
    assert fks[0].target_fullname == "eval_datasets.id"
    assert fks[0].ondelete == "CASCADE"


def test_item_provenance_fks_set_null() -> None:
    """Provenance back to the real task/execution — promotion is
    idempotent (task_14_02 dedupes on these); the golden item survives the
    real task it was promoted from.

    ``source_execution_id`` **no longer carries a foreign key** since part-01 /
    ADR 0154 (migration 0137): ``executions`` is partitioned by month, so its
    primary key is ``(id, created_at)`` and a FK cannot reference it without
    carrying both columns. What the ``SET NULL`` bought — the golden item
    surviving the run it was promoted from — is *more* true now, not less: the
    column was already nullable and no reader assumes the run still exists. The
    provenance FK that remains, ``source_task_id``, is unaffected.
    """
    task_fks = list(EvalDatasetItem.__table__.columns["source_task_id"].foreign_keys)
    assert task_fks[0].target_fullname == "tasks.id"
    assert task_fks[0].ondelete == "SET NULL"

    exec_fks = list(EvalDatasetItem.__table__.columns["source_execution_id"].foreign_keys)
    assert not exec_fks, (
        "source_execution_id volvió a declarar una FK hacia executions. La tabla"
        " está particionada (PK compuesta) y esa constraint no se puede crear:"
        " la migración fallaría. Ver ADR 0154."
    )
    assert EvalDatasetItem.__table__.columns["source_execution_id"].nullable


def test_item_construction() -> None:
    item = EvalDatasetItem(
        tenant_id=uuid4(),
        dataset_id=uuid4(),
        input={"prompt": "Refactor this function", "files": ["a.py"]},
        expected_output="def f(): ...",
        source_task_id=uuid4(),
        source_execution_id=uuid4(),
    )
    assert item.input["prompt"] == "Refactor this function"
    assert item.expected_output == "def f(): ..."


# ---------------------------------------------------------------------------
# EvalCriterion — rubric + weight + threshold
# ---------------------------------------------------------------------------
def test_criterion_columns() -> None:
    cols = {c.name for c in EvalCriterion.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "dataset_id",
        "name",
        "description",
        "judge_instruction",
        "weight",
        "pass_threshold",
    } <= cols


def test_criterion_dataset_fk_cascades() -> None:
    fks = list(EvalCriterion.__table__.columns["dataset_id"].foreign_keys)
    assert fks[0].target_fullname == "eval_datasets.id"
    assert fks[0].ondelete == "CASCADE"


def test_criterion_threshold_and_weight_constraints() -> None:
    names = {c.name for c in EvalCriterion.__table__.constraints}
    assert "ck_eval_criteria_weight_non_negative" in names
    assert "ck_eval_criteria_pass_threshold_unit_range" in names


def test_criterion_construction_defaults() -> None:
    crit = EvalCriterion(
        tenant_id=uuid4(),
        dataset_id=uuid4(),
        name="PEP 8",
        judge_instruction="Does the produced code follow PEP 8 style?",
    )
    assert crit.name == "PEP 8"
    # weight / pass_threshold carry server defaults (NULL pre-flush).
    assert EvalCriterion.__table__.columns["weight"].server_default is not None
    assert EvalCriterion.__table__.columns["pass_threshold"].server_default is not None


# ---------------------------------------------------------------------------
# EvalRun — subject + status + aggregate metrics
# ---------------------------------------------------------------------------
def test_run_columns() -> None:
    cols = {c.name for c in EvalRun.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "dataset_id",
        "status",
        "subject_agent_id",
        "subject_prompt_version",
        "judge_model",
        "started_at",
        "finished_at",
        "total_items",
        "passed_items",
        "pass_rate",
        "mean_latency_ms",
        "mean_tokens",
        "mean_cost_usd",
        "aggregate_metrics",
    } <= cols


def test_run_dataset_fk_cascades() -> None:
    fks = list(EvalRun.__table__.columns["dataset_id"].foreign_keys)
    assert fks[0].target_fullname == "eval_datasets.id"
    assert fks[0].ondelete == "CASCADE"


def test_run_subject_agent_fk_sets_null() -> None:
    fks = list(EvalRun.__table__.columns["subject_agent_id"].foreign_keys)
    assert fks[0].target_fullname == "agents.id"
    assert fks[0].ondelete == "SET NULL"


def test_run_construction_defaults() -> None:
    run = EvalRun(tenant_id=uuid4(), dataset_id=uuid4())
    assert EvalRun.__table__.columns["status"].server_default is not None
    assert EvalRun.__table__.columns["total_items"].server_default is not None
    # mean_cost_usd is a Decimal-mapped (Numeric) column.
    run.mean_cost_usd = Decimal("0.001234")
    assert run.mean_cost_usd == Decimal("0.001234")


# ---------------------------------------------------------------------------
# EvalResult — per-criterion scores + verdict + usage
# ---------------------------------------------------------------------------
def test_result_columns() -> None:
    cols = {c.name for c in EvalResult.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "run_id",
        "item_id",
        "produced_output",
        "criterion_scores",
        "verdict",
        "overall_score",
        "latency_ms",
        "tokens",
        "cost_usd",
    } <= cols


def test_result_run_fk_cascades() -> None:
    fks = list(EvalResult.__table__.columns["run_id"].foreign_keys)
    assert fks[0].target_fullname == "eval_runs.id"
    assert fks[0].ondelete == "CASCADE"


def test_result_item_fk_sets_null() -> None:
    fks = list(EvalResult.__table__.columns["item_id"].foreign_keys)
    assert fks[0].target_fullname == "eval_dataset_items.id"
    assert fks[0].ondelete == "SET NULL"


def test_result_per_criterion_score_shape() -> None:
    """The per-criterion-score shape the rest of Plan 14 builds against:
    a list of {criterion_id, score, passed, rationale} dicts."""
    result = EvalResult(
        tenant_id=uuid4(),
        run_id=uuid4(),
        item_id=uuid4(),
        produced_output="def f(): return 1",
        criterion_scores=[
            {
                "criterion_id": str(uuid4()),
                "score": 0.92,
                "passed": True,
                "rationale": "Follows PEP 8; no lint errors.",
            },
            {
                "criterion_id": str(uuid4()),
                "score": 0.40,
                "passed": False,
                "rationale": "Tone too informal.",
            },
        ],
        verdict=EvalResultVerdict.FAIL,
        overall_score=Decimal("0.660"),
        latency_ms=1234,
        tokens=2048,
        cost_usd=Decimal("0.004500"),
    )
    assert result.verdict == "fail"
    assert len(result.criterion_scores) == 2
    first = result.criterion_scores[0]
    assert set(first) >= {"criterion_id", "score", "passed", "rationale"}
    assert first["passed"] is True
    assert result.tokens == 2048


def test_result_default_verdict_and_scores() -> None:
    assert EvalResult.__table__.columns["verdict"].server_default is not None
    assert EvalResult.__table__.columns["criterion_scores"].server_default is not None


# ---------------------------------------------------------------------------
# Relationships — dataset -> criteria / items / runs; run -> results
# ---------------------------------------------------------------------------
def test_dataset_relationships() -> None:
    rels = EvalDataset.__mapper__.relationships
    assert rels["criteria"].mapper.class_ is EvalCriterion
    assert rels["items"].mapper.class_ is EvalDatasetItem
    assert rels["runs"].mapper.class_ is EvalRun


def test_run_results_relationship() -> None:
    assert EvalRun.__mapper__.relationships["results"].mapper.class_ is EvalResult


def test_item_results_relationship() -> None:
    assert EvalDatasetItem.__mapper__.relationships["results"].mapper.class_ is EvalResult


def test_relationship_round_trip_in_memory() -> None:
    """Build the object graph in memory (no DB) and verify back_populates."""
    tid = uuid4()
    ds = EvalDataset(tenant_id=tid, name="golden", kind=EvalDatasetKind.GOLDEN)
    crit = EvalCriterion(
        tenant_id=tid,
        dataset=ds,
        name="correctness",
        judge_instruction="Is the output correct?",
    )
    item = EvalDatasetItem(tenant_id=tid, dataset=ds, input={"prompt": "x"})
    run = EvalRun(tenant_id=tid, dataset=ds, status=EvalRunStatus.PENDING)
    result = EvalResult(tenant_id=tid, run=run, item=item, verdict=EvalResultVerdict.PASS)

    assert crit in ds.criteria
    assert item in ds.items
    assert run in ds.runs
    assert result in run.results
    assert result in item.results
    assert result.run is run
    assert result.item is item
