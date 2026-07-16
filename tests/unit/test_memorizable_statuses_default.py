"""AUD16-17 (auditoría 2026-07-16): el default OPERATIVO de estados memorizables
incluye los fracasos.

P1-1(a) (investigación 2026-07-11) cambió el default de la política pura
(``policy._DEFAULT_ELIGIBLE_STATUSES``) para que failed/aborted/
needs_human_review también dejen lección — pero el único caller de producción
(el worker del Memorizer) pasa el resultado de ``get_memorizable_statuses()``,
cuyo default (``DEFAULT_MEMORY_MEMORIZABLE_STATUSES``) seguía siendo
``("done",)``: sin fila en ``platform_settings``, el camino real nunca vio el
default nuevo. Este test fija el invariante: ambos defaults son EL MISMO
conjunto.
"""

from __future__ import annotations

import pytest
from api_server.db.platform_settings import DEFAULT_MEMORY_MEMORIZABLE_STATUSES
from api_server.memorizer.policy import _DEFAULT_ELIGIBLE_STATUSES

pytestmark = pytest.mark.unit


def test_platform_default_matches_policy_default() -> None:
    assert set(DEFAULT_MEMORY_MEMORIZABLE_STATUSES) == set(_DEFAULT_ELIGIBLE_STATUSES)


def test_default_includes_failure_statuses() -> None:
    assert {"done", "failed", "aborted", "needs_human_review"} <= set(
        DEFAULT_MEMORY_MEMORIZABLE_STATUSES
    )
