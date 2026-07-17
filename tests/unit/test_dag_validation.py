"""Unit tests for the plan-task DAG validator (Plan 03 task_03_15).

Pure-Python — no DB. The validator runs inside the persistence path
of `POST /projects/{project_id}/plans` so a cycle never lands in the
database, and as a standalone helper the planning sub-graph can also
call before emitting a draft.
"""

from __future__ import annotations

import pytest
from api_server.chat.dag import (
    DAGCycleError,
    TaskRef,
    assert_acyclic_with_override,
    validate_dag,
)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------
def test_empty_graph_is_valid() -> None:
    validate_dag([])  # no exception


def test_linear_chain_is_valid() -> None:
    validate_dag(
        [
            TaskRef(id="a", depends_on=()),
            TaskRef(id="b", depends_on=("a",)),
            TaskRef(id="c", depends_on=("b",)),
        ]
    )


def test_diamond_dependency_is_valid() -> None:
    """``a`` -> ``b`` and ``a`` -> ``c``, both -> ``d``."""
    validate_dag(
        [
            TaskRef(id="a", depends_on=()),
            TaskRef(id="b", depends_on=("a",)),
            TaskRef(id="c", depends_on=("a",)),
            TaskRef(id="d", depends_on=("b", "c")),
        ]
    )


def test_dict_input_is_accepted() -> None:
    """Callers can pass the raw spec's `tasks` list without converting
    to `TaskRef` first."""
    validate_dag(
        [
            {"id": "a", "depends_on": []},
            {"id": "b", "depends_on": ["a"]},
        ]
    )


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------
def test_two_node_cycle_is_rejected() -> None:
    with pytest.raises(DAGCycleError) as info:
        validate_dag(
            [
                TaskRef(id="a", depends_on=("b",)),
                TaskRef(id="b", depends_on=("a",)),
            ]
        )
    # cycle is reported as `a -> b -> a` (or `b -> a -> b`, both are
    # valid descriptions of the same back-edge).
    assert info.value.cycle[0] == info.value.cycle[-1]
    assert {info.value.cycle[0], info.value.cycle[1]} == {"a", "b"}


def test_three_node_cycle_is_rejected() -> None:
    with pytest.raises(DAGCycleError):
        validate_dag(
            [
                TaskRef(id="a", depends_on=("b",)),
                TaskRef(id="b", depends_on=("c",)),
                TaskRef(id="c", depends_on=("a",)),
            ]
        )


def test_cycle_off_a_dag_root_is_still_caught() -> None:
    """A graph that is mostly DAG-shaped with a small cycle in a
    subgraph still gets rejected — partial DAG-ness is not enough."""
    with pytest.raises(DAGCycleError):
        validate_dag(
            [
                TaskRef(id="root", depends_on=()),
                TaskRef(id="a", depends_on=("root", "c")),
                TaskRef(id="b", depends_on=("a",)),
                TaskRef(id="c", depends_on=("b",)),
            ]
        )


def test_self_loop_is_rejected() -> None:
    """Pydantic catches self-loops upstream, but the validator must
    not silently accept them either."""
    with pytest.raises(DAGCycleError):
        validate_dag([TaskRef(id="a", depends_on=("a",))])


# ---------------------------------------------------------------------------
# Input shape
# ---------------------------------------------------------------------------
def test_duplicate_task_ids_raises_value_error() -> None:
    with pytest.raises(ValueError, match="duplicate task id"):
        validate_dag([TaskRef(id="a", depends_on=()), TaskRef(id="a", depends_on=())])


def test_dict_input_without_id_raises_value_error() -> None:
    with pytest.raises(ValueError, match="string `id`"):
        validate_dag([{"depends_on": []}])


def test_unknown_dependency_does_not_raise_a_dag_error() -> None:
    """Unknown deps are a Pydantic-layer concern. The DAG check just
    skips them so it never falsely reports a cycle."""
    validate_dag(
        [
            TaskRef(id="a", depends_on=("does-not-exist",)),
        ]
    )


# ---------------------------------------------------------------------------
# PROY2-04: cycle detection across multiple PUTs (overlay one node's deps on
# the existing project-wide edge set).
# ---------------------------------------------------------------------------
def test_override_that_closes_a_cycle_is_rejected() -> None:
    # Existing: a -> b (a depends on b). Now set b -> a → cycle.
    existing = {"a": ("b",), "b": ()}
    with pytest.raises(DAGCycleError):
        assert_acyclic_with_override(existing, "b", ["a"])


def test_override_that_keeps_the_graph_acyclic_is_ok() -> None:
    existing = {"a": (), "b": ("a",), "c": ()}
    # c -> b keeps it a DAG (c depends on b depends on a).
    assert_acyclic_with_override(existing, "c", ["b"])


def test_override_replaces_the_nodes_previous_edges() -> None:
    # a used to depend on b (cycle if kept), but the override drops that edge.
    existing = {"a": ("b",), "b": ("a",)}  # already cyclic on paper
    # Overriding a to depend on nothing breaks the cycle.
    assert_acyclic_with_override(existing, "a", [])
