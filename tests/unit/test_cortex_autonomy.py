"""Córtex F4 — gobierno autónomo: helpers puros (sin Redis, sin red).

Las funciones puras de :mod:`api_server.cortex.autonomy` (claves Redis +
``seconds_until_utc_midnight``) deben ser deterministas y correctas en los bordes
(medianoche UTC, cambio de día). El comportamiento Redis-backed (budget gate +
circuit-breaker) se ejercita en el test de integración con el Redis de test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_server.cortex.autonomy import (
    CURIOSITY_KIND,
    circuit_fails_key,
    circuit_key,
    daily_budget_key,
    seconds_until_utc_midnight,
)

pytestmark = pytest.mark.unit


def test_daily_budget_key_includes_owner_kind_and_day() -> None:
    now = datetime(2026, 6, 24, 13, 30, tzinfo=UTC)
    key = daily_budget_key("owner-1", CURIOSITY_KIND, now=now)
    assert key == "cortex:budget:owner-1:curiosity:20260624"


def test_daily_budget_key_uses_utc_day_not_local() -> None:
    # Un instante naïve/aware se normaliza a UTC para fijar la ventana del día.
    from datetime import timedelta, timezone

    plus_two = timezone(timedelta(hours=2))
    # 00:30 en UTC+2 es 22:30 del día ANTERIOR en UTC → la ventana es el día previo.
    now = datetime(2026, 6, 24, 0, 30, tzinfo=plus_two)
    key = daily_budget_key("o", CURIOSITY_KIND, now=now)
    assert key.endswith(":20260623")


def test_circuit_keys_are_owner_and_kind_scoped() -> None:
    assert circuit_key("o", "curiosity") == "cortex:cb:o:curiosity"
    assert circuit_fails_key("o", "curiosity") == "cortex:cb:o:curiosity:fails"
    # Cross-owner: nunca colisionan las claves de dos owners.
    assert circuit_key("a", "curiosity") != circuit_key("b", "curiosity")


def test_seconds_until_utc_midnight_basic() -> None:
    now = datetime(2026, 6, 24, 23, 0, tzinfo=UTC)
    # 1 hora hasta medianoche.
    assert seconds_until_utc_midnight(now) == 3600


def test_seconds_until_utc_midnight_just_after_midnight() -> None:
    now = datetime(2026, 6, 24, 0, 0, 1, tzinfo=UTC)
    # Casi un día completo (86400 - 1).
    assert seconds_until_utc_midnight(now) == 86399


def test_seconds_until_utc_midnight_never_zero() -> None:
    # Exactamente medianoche → la próxima medianoche es +1 día (no 0, que borraría
    # la clave al instante).
    now = datetime(2026, 6, 24, 0, 0, 0, tzinfo=UTC)
    assert seconds_until_utc_midnight(now) == 86400
