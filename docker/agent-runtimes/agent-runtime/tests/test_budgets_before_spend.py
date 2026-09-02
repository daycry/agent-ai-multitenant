"""Los presupuestos se aplican ANTES del gasto (`task_cv_40`, auditoría 2026-09-01).

Tres huecos medidos (D-05, D-06, D-07):

- El wall-clock sólo se miraba entre iteraciones: una llamada al proveedor
  podía rebasarlo 45 min y era el worker quien mataba el contenedor. Ahora el
  cliente del proveedor conoce el restante y lo usa como `timeout` de la
  llamada.
- `max_cost_usd` era 0 en tres de cuatro proveedores (sólo el que devuelve
  coste lo sumaba), así que el techo de coste no tripaba nunca. Si el
  proveedor devuelve 0 y el spec trae precios del catálogo, se estima.
- Un fallo del motor de guardrails dejaba un proyecto con reglas `block`
  corriendo SIN ninguna. Si el spec declara `block` y el pipeline no arranca,
  el run aborta (`guardrails_unavailable`) en vez de correr a ciegas.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_runtime.guardrails import abort_if_unscreened_block, declares_block
from agent_runtime.safeguards import Budgets, ModelPrices, SafeguardCode, SafeguardTracker

# ------------------------------------------------------------- coste estimado


def test_a_zero_cost_response_is_estimated_from_the_catalog_prices() -> None:
    prices = ModelPrices(input_usd_per_1m=3.0, output_usd_per_1m=15.0)
    tracker = SafeguardTracker(Budgets(max_cost_usd=1.0), prices=prices)

    tracker.record_model_call(tokens_in=100_000, tokens_out=10_000, cost_usd=0.0)

    assert tracker.usage.cost_usd == pytest.approx(0.45)
    assert tracker.usage.cost_estimated_calls == 1


def test_the_cost_ceiling_trips_on_estimated_cost() -> None:
    prices = ModelPrices(input_usd_per_1m=3.0, output_usd_per_1m=15.0)
    tracker = SafeguardTracker(Budgets(max_cost_usd=0.25), prices=prices)

    tracker.record_model_call(tokens_in=100_000, tokens_out=0, cost_usd=0.0)

    assert tracker.check() == SafeguardCode.MAX_COST


def test_a_provider_that_reports_cost_is_not_second_guessed() -> None:
    prices = ModelPrices(input_usd_per_1m=3.0, output_usd_per_1m=15.0)
    tracker = SafeguardTracker(Budgets(), prices=prices)

    tracker.record_model_call(tokens_in=100_000, tokens_out=0, cost_usd=0.01)

    assert tracker.usage.cost_usd == 0.01
    assert tracker.usage.cost_estimated_calls == 0


def test_without_prices_zero_stays_zero_as_before() -> None:
    tracker = SafeguardTracker(Budgets())

    tracker.record_model_call(tokens_in=100_000, tokens_out=0, cost_usd=0.0)

    assert tracker.usage.cost_usd == 0.0


def test_prices_come_from_the_spec_and_tolerate_absence() -> None:
    assert ModelPrices.from_spec(None) is None
    assert ModelPrices.from_spec({}) is None
    assert ModelPrices.from_spec({"input_usd_per_1m": "x"}) is None
    parsed = ModelPrices.from_spec({"input_usd_per_1m": "3", "output_usd_per_1m": 15})
    assert parsed == ModelPrices(input_usd_per_1m=3.0, output_usd_per_1m=15.0)


def test_the_usage_envelope_reports_estimated_calls() -> None:
    tracker = SafeguardTracker(Budgets(), prices=ModelPrices(1.0, 1.0))
    tracker.record_model_call(tokens_in=10, tokens_out=10, cost_usd=0.0)

    assert tracker.usage.as_dict()["cost_estimated_calls"] == 1


# ------------------------------------------------------------- guardrails block


def _spec_with(action: str, *, on_error: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {
        "guardrails": {
            "pre_llm": [{"type": "prompt_injection", "action": "warn", "config": {}}],
            "post_tool": [{"type": "prompt_injection", "action": action, "config": {}}],
        }
    }
    if on_error is not None:
        source["on_error"] = on_error
    return {"task": {"title": "x"}, "guardrails": source}


def test_a_block_rule_anywhere_declares_block() -> None:
    assert declares_block(_spec_with("block")["guardrails"]) is True
    assert declares_block(_spec_with("warn")["guardrails"]) is False
    assert declares_block(_spec_with("warn", on_error="block")["guardrails"]) is True
    assert declares_block(None) is False
    assert declares_block({"guardrails": {}}) is False


def test_a_block_policy_without_engine_aborts_instead_of_running_unscreened() -> None:
    result = abort_if_unscreened_block(_spec_with("block"), pipeline=None)

    assert result is not None
    assert result["status"] == "aborted"
    assert result["abort_code"] == "guardrails_unavailable"
    assert "block" in (result["output"] or "")


def test_a_warn_only_policy_without_engine_still_runs() -> None:
    assert abort_if_unscreened_block(_spec_with("warn"), pipeline=None) is None


def test_with_a_working_engine_nothing_aborts() -> None:
    assert abort_if_unscreened_block(_spec_with("block"), pipeline=object()) is None
