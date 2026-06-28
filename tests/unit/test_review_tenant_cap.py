"""Unit test: the production-path tenant cap on review-runtimes (C8 F41).

``compose_review_runtime`` now refuses the N+1-th concurrent runtime for a tenant
BEFORE spawning. The DB count + the early return are integration-tested; here we
pin the pure boundary decision.
"""

from __future__ import annotations

import pytest
from workers.review_runtime import DEFAULT_TENANT_CAP
from workers.tasks import tenant_cap_exceeded

pytestmark = pytest.mark.unit


def test_under_cap_allows_spawn() -> None:
    assert tenant_cap_exceeded(0, 5) is False
    assert tenant_cap_exceeded(4, 5) is False


def test_at_or_over_cap_rejects_the_next() -> None:
    # The 6th (N+1) when 5 already run is refused.
    assert tenant_cap_exceeded(5, 5) is True
    assert tenant_cap_exceeded(6, 5) is True


def test_uses_default_cap_of_five() -> None:
    assert DEFAULT_TENANT_CAP == 5
    assert tenant_cap_exceeded(DEFAULT_TENANT_CAP, DEFAULT_TENANT_CAP) is True
    assert tenant_cap_exceeded(DEFAULT_TENANT_CAP - 1, DEFAULT_TENANT_CAP) is False
