"""PROY2-01/02 (auditoría proyecto 2026-07-17): la superficie genérica de
planes no puede gobernar el ciclo de vida saltándose los gates.

`POST /plans` aceptaba cualquier `status` inicial (nacer approved/completed) y
`PUT /plans/{id}` (solo require_tenant_member) ejecutaba transiciones
PRIVILEGIADAS por la mera tabla de adyacencia: aprobar sin rol/doble firma,
completar sin veredicto humano. Esas transiciones pertenecen a endpoints con
gate (`POST /approve`, submit_verdict). Guards puros aquí; el router los usa.
"""

from __future__ import annotations

import pytest
from api_server.chat.plan_state_machine import (
    PRIVILEGED_PUT_TARGETS,
    PlanPutForbiddenError,
    assert_generic_put_transition,
)
from api_server.db.domain import PlanStatus

pytestmark = pytest.mark.unit


def test_privileged_targets_are_the_gated_ones() -> None:
    assert (
        frozenset(
            {
                PlanStatus.APPROVED.value,
                PlanStatus.PENDING_SECOND_APPROVAL.value,
                PlanStatus.COMPLETED.value,
            }
        )
        == PRIVILEGED_PUT_TARGETS
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("pending_approval", "approved"),
        ("pending_approval", "pending_second_approval"),
        ("pending_human_validation", "completed"),
    ],
)
def test_generic_put_refuses_privileged_transition(current: str, target: str) -> None:
    with pytest.raises(PlanPutForbiddenError) as info:
        assert_generic_put_transition(current, target)
    assert info.value.to_status == target
    assert info.value.endpoint  # apunta al endpoint correcto


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("draft", "pending_approval"),
        ("in_progress", "cancelled"),
        ("blocked", "in_progress"),
        ("rejected", "draft"),
        ("completed", "archived"),
    ],
)
def test_generic_put_allows_non_privileged_transitions(current: str, target: str) -> None:
    # No lanza — estas transiciones NO están gated en otro endpoint.
    assert_generic_put_transition(current, target)


def test_no_status_change_is_allowed() -> None:
    assert_generic_put_transition("in_progress", "in_progress")
