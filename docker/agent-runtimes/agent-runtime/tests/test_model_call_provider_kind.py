"""AUD16-15 (auditoría 2026-07-16): cada step model_call lleva el KIND del
proveedor del run — sin él, el price-snapshot del api-server buscaba en el
catálogo con provider="" y el coste facturable quedó NULL en el 100% de las
executions."""

from __future__ import annotations

from agent_runtime.steps import model_call_step


def test_model_call_step_records_provider_kind() -> None:
    step = model_call_step(
        0,
        "plan",
        model="claude-opus-4-8",
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.01,
        summary="s",
        provider="claude_sdk",
    )
    assert step["provider"] == "claude_sdk"


def test_model_call_step_without_provider_keeps_legacy_shape() -> None:
    step = model_call_step(
        0, "plan", model="m", tokens_in=1, tokens_out=1, cost_usd=0.0, summary="s"
    )
    assert "provider" not in step
