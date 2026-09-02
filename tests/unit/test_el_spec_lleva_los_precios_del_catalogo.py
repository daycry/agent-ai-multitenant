"""El run spec lleva los precios del catálogo para el modelo efectivo (`task_cv_40`).

Auditoría 2026-09-01 (D-06): `max_cost_usd` era 0 en tres de cuatro proveedores
porque sólo el que devuelve coste lo sumaba. El worker, que ya resuelve el
modelo contra la BD, adjunta `model.prices` (USD por millón de tokens) cuando
el catálogo (`model_prices`, ADR 0021 / task_11_18) tiene una fila abierta, y
el runtime estima el coste de cada llamada que llega a 0.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from workers.execution import _price_hint

pytestmark = pytest.mark.unit


def _row(unit: str = "per_1m_tokens", **over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "input_price": Decimal("3.0"),
        "output_price": Decimal("15.0"),
        "unit": unit,
        "currency": "USD",
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_the_hint_carries_usd_per_million_tokens() -> None:
    catalog = {"claude-sonnet-5": _row()}

    hint = _price_hint(catalog.get, ["claude-sonnet-5"])

    assert hint == {"input_usd_per_1m": 3.0, "output_usd_per_1m": 15.0}


def test_per_1k_prices_are_scaled_to_per_million() -> None:
    catalog = {
        "m": _row(unit="per_1k_tokens", input_price=Decimal("0.003"), output_price=Decimal("0.015"))
    }

    hint = _price_hint(catalog.get, ["m"])

    assert hint == {"input_usd_per_1m": 3.0, "output_usd_per_1m": 15.0}


def test_the_first_known_id_wins_so_provider_aliases_still_resolve() -> None:
    """El id resuelto para el proveedor (`to_provider_model_name`) puede no ser
    el del catálogo; se prueba también el id que pidió el proyecto."""
    catalog = {"claude-sonnet-5": _row()}

    hint = _price_hint(catalog.get, ["claude-sonnet-5-20260801", "claude-sonnet-5"])

    assert hint is not None and hint["input_usd_per_1m"] == 3.0


def test_without_a_catalog_row_there_is_no_hint() -> None:
    assert _price_hint({}.get, ["unknown"]) is None
    assert _price_hint({}.get, []) is None


def test_a_non_usd_row_is_not_used() -> None:
    catalog = {"m": _row(currency="EUR")}

    assert _price_hint(catalog.get, ["m"]) is None
