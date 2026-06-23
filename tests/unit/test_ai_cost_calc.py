"""Unit tests for the AI cost calculator (Plan 03 task_03_23)."""

from __future__ import annotations

from decimal import Decimal

from api_server.chat.cost import (
    DEFAULT_AI_PRICE_CATALOG,
    DEFAULT_COMPLEXITY_ESTIMATES,
    ComplexityTokenEstimate,
    ModelPrice,
    PriceCatalog,
    compute_ai_cost,
)


def test_empty_spec_returns_zero_range() -> None:
    result = compute_ai_cost({}, default_model_id="gpt-4o")
    assert result.cost_min == Decimal("0.0000")
    assert result.cost_max == Decimal("0.0000")
    assert result.tasks == ()
    assert result.missing_models == ()
    assert result.default_model_id == "gpt-4o"
    # Currency comes from the default model (USD in the catalog).
    assert result.currency == "USD"


def test_single_task_uses_default_model_when_no_task_override() -> None:
    spec = {"tasks": [{"id": "t1", "title": "A", "complexity": "m"}]}
    result = compute_ai_cost(spec, default_model_id="gpt-4o")
    assert len(result.tasks) == 1
    row = result.tasks[0]
    assert row.model_id == "gpt-4o"
    # m: 15k input × ($2.5/1M) = $0.0375, 4k output × ($10/1M) = $0.04
    # midpoint cost = $0.0775; range factors 0.5 / 1.5
    # min = 0.5 × 0.0775 = 0.03875 ≈ 0.0388
    # max = 1.5 × 0.0775 = 0.11625 ≈ 0.1163
    assert row.cost_min == Decimal("0.0388")
    assert row.cost_max == Decimal("0.1163")
    # Total matches single-task range.
    assert result.cost_min == row.cost_min
    assert result.cost_max == row.cost_max


def test_min_is_below_max_for_every_complexity() -> None:
    spec = {
        "tasks": [
            {"id": f"t-{c}", "title": c, "complexity": c} for c in DEFAULT_COMPLEXITY_ESTIMATES
        ]
    }
    result = compute_ai_cost(spec, default_model_id="claude-sonnet-4-6")
    for row in result.tasks:
        assert row.cost_min < row.cost_max
        assert row.tokens_in_min < row.tokens_in_max
        assert row.tokens_out_min < row.tokens_out_max


def test_total_is_the_sum_of_per_task_ranges() -> None:
    spec = {
        "tasks": [
            {"id": "t1", "title": "A", "complexity": "s"},
            {"id": "t2", "title": "B", "complexity": "l"},
            {"id": "t3", "title": "C", "complexity": "m"},
        ],
    }
    result = compute_ai_cost(spec, default_model_id="claude-sonnet-4-6")
    expected_min = sum((t.cost_min for t in result.tasks), Decimal("0"))
    expected_max = sum((t.cost_max for t in result.tasks), Decimal("0"))
    assert result.cost_min == expected_min
    assert result.cost_max == expected_max


def test_task_level_model_override_takes_precedence() -> None:
    """A task can pin its own model; the override beats the default."""
    spec = {
        "tasks": [
            {"id": "t1", "title": "Expensive", "complexity": "m", "model": "claude-opus-4-7"},
            {"id": "t2", "title": "Cheap", "complexity": "m"},
        ],
    }
    result = compute_ai_cost(spec, default_model_id="gpt-4o")
    by_id = {t.task_id: t for t in result.tasks}
    assert by_id["t1"].model_id == "claude-opus-4-7"
    assert by_id["t2"].model_id == "gpt-4o"
    # Opus is more expensive than gpt-4o on every dimension.
    assert by_id["t1"].cost_min > by_id["t2"].cost_min


def test_task_models_override_beats_task_model_and_default() -> None:
    """A per-task resolved model (e.g. the model of the agent assigned to the
    task, override or inherited — ADR 0065) takes top precedence: it beats both
    the task's own ``model`` field and the plan ``default_model_id``. This is
    how the cost-breakdown stops pricing everything as ``gpt-4o`` and instead
    uses each task's real assigned model."""
    spec = {
        "tasks": [
            {"id": "t1", "title": "Has own model", "complexity": "m", "model": "gpt-4o"},
            {"id": "t2", "title": "No own model", "complexity": "m"},
            {"id": "t3", "title": "Not in the map", "complexity": "m"},
        ],
    }
    result = compute_ai_cost(
        spec,
        default_model_id="gpt-4o",
        task_models={"t1": "claude-opus-4-7", "t2": "claude-opus-4-7"},
    )
    by_id = {t.task_id: t for t in result.tasks}
    assert by_id["t1"].model_id == "claude-opus-4-7"  # beats the task's own "gpt-4o"
    assert by_id["t2"].model_id == "claude-opus-4-7"  # beats the default
    assert by_id["t3"].model_id == "gpt-4o"  # not in the map → falls back to default
    # Opus is more expensive than gpt-4o, so the resolved tasks cost more.
    assert by_id["t1"].cost_min > by_id["t3"].cost_min


def test_task_models_empty_or_none_is_a_noop() -> None:
    spec = {"tasks": [{"id": "t1", "title": "A", "complexity": "m"}]}
    base = compute_ai_cost(spec, default_model_id="gpt-4o")
    for tm in (None, {}, {"other": "claude-opus-4-7"}):
        result = compute_ai_cost(spec, default_model_id="gpt-4o", task_models=tm)
        assert result.tasks[0].model_id == base.tasks[0].model_id == "gpt-4o"


def test_unknown_complexity_falls_back_to_default() -> None:
    spec = {"tasks": [{"id": "t1", "title": "Weird", "complexity": "huge"}]}
    result = compute_ai_cost(spec, default_model_id="gpt-4o", default_complexity="m")
    # Falls back to "m" — same cost as if complexity had been "m".
    expected = compute_ai_cost(
        {"tasks": [{"id": "t1", "title": "Weird", "complexity": "m"}]},
        default_model_id="gpt-4o",
    )
    assert result.tasks[0].cost_min == expected.tasks[0].cost_min
    assert result.tasks[0].cost_max == expected.tasks[0].cost_max
    # And the complexity field is normalised.
    assert result.tasks[0].complexity == "m"


def test_missing_model_lands_in_missing_models_with_zero_cost() -> None:
    spec = {"tasks": [{"id": "t1", "title": "A", "complexity": "m", "model": "phantom-9"}]}
    result = compute_ai_cost(spec, default_model_id="gpt-4o")
    assert result.tasks[0].cost_min == Decimal("0.0000")
    assert result.tasks[0].cost_max == Decimal("0.0000")
    assert result.missing_models == ("phantom-9",)


def test_ollama_local_is_priced_at_zero() -> None:
    """A local Ollama deployment has no API cost; the GPU bill is the
    operator's responsibility — surfaced as 0 by design."""
    spec = {"tasks": [{"id": "t1", "title": "A", "complexity": "l"}]}
    result = compute_ai_cost(spec, default_model_id="llama3.1")
    assert result.tasks[0].cost_min == Decimal("0.0000")
    assert result.tasks[0].cost_max == Decimal("0.0000")


def test_currency_follows_the_default_model_entry() -> None:
    """A tenant priced in EUR (custom catalog) returns EUR totals."""
    custom = PriceCatalog(
        prices={
            "my-eur-model": ModelPrice(
                "my-eur-model",
                currency="EUR",
                input_per_million=Decimal("4"),
                output_per_million=Decimal("12"),
            )
        }
    )
    result = compute_ai_cost(
        {"tasks": [{"id": "t1", "title": "x", "complexity": "m"}]},
        default_model_id="my-eur-model",
        catalog=custom,
    )
    assert result.currency == "EUR"


def test_custom_complexity_estimates_are_respected() -> None:
    """A tenant with tighter token budgets gets a tighter cost range."""
    tight = {
        "m": ComplexityTokenEstimate(
            "m",
            base_input_tokens=1_000,
            base_output_tokens=200,
            low_factor=Decimal("0.8"),
            high_factor=Decimal("1.2"),
        ),
    }
    spec = {"tasks": [{"id": "t1", "title": "A", "complexity": "m"}]}
    result = compute_ai_cost(
        spec,
        default_model_id="gpt-4o",
        complexity_estimates=tight,
    )
    # in: 0.8*1000=800 ... 1.2*1000=1200 -> input cost min
    # = 800/1e6 × 2.5 = 0.002; out = 0.8*200=160 → 160/1e6 × 10 = 0.0016
    # cost_min ≈ 0.0036; checks the path uses our estimates, not the default.
    assert result.tasks[0].cost_min == Decimal("0.0036")


def test_default_catalog_has_the_four_provider_models() -> None:
    """Every model in the catálogo cerrado (ADR 0021) shows up here."""
    expected = {
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "gpt-4o",
        "llama3.1",
    }
    assert expected.issubset(DEFAULT_AI_PRICE_CATALOG.prices.keys())
