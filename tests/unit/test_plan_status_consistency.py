"""Contract test: the two PlanStatus vocabularies must stay identical (c10).

`api_server.db.domain.PlanStatus` (StrEnum) is the authoritative lifecycle set.
`api_server.plan_progress.PlanStatus` is a `Literal` mirror kept so the pure,
DB-agnostic transition helpers need not import the SQLAlchemy domain module. The
audit (2026-07-03, c10) found they had diverged (`draft` /
`pending_second_approval` missing from the Literal). This pins them equal so a
future edit to one without the other fails CI.
"""

from __future__ import annotations

from typing import get_args

from api_server.db.domain import PlanStatus as DomainPlanStatus
from api_server.plan_progress import PlanStatus as LiteralPlanStatus


def test_plan_status_literal_matches_domain_enum() -> None:
    domain_values = {member.value for member in DomainPlanStatus}
    literal_values = set(get_args(LiteralPlanStatus))
    assert literal_values == domain_values, (
        "PlanStatus vocabularies diverged: "
        f"only in domain={domain_values - literal_values}, "
        f"only in plan_progress={literal_values - domain_values}"
    )
