"""Informe de reutilización de caché de prompt (`task_wf_63`).

La tarea es de MEDICIÓN, no de optimización, y el orden importa: pasar
`_decide_messages` de «un mensaje grande reconstruido cada turno» a una lista
incremental es un cambio con riesgo real sobre la convergencia. Antes de
tocarlo hay que saber si la caché de prefijo sirve de algo, y en qué proveedor.

Lo que estos tests protegen sobre todo es la HONESTIDAD del informe: un cero que
significa «no hay reutilización» y un cero que significa «este proveedor no lo
reporta» no se pueden confundir, porque llevarían a optimizar a ciegas.
"""

from __future__ import annotations

import pytest
from api_server.prompt_cache_report import build_prompt_cache_report

pytestmark = pytest.mark.unit


def _call(provider: str, *, tin: int, tout: int = 100, cost: float = 0.01, cached: int = 0):
    step = {
        "kind": "model_call",
        "provider": provider,
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": cost,
    }
    if cached:
        step["cache_read_tokens"] = cached
    return step


def _by_provider(report) -> dict:
    return {row["provider"]: row for row in report.as_dict()["providers"]}


def test_the_reuse_percentage_is_over_the_prompt_tokens() -> None:
    report = build_prompt_cache_report(
        [(None, [_call("claude_sdk", tin=1000, cached=800), _call("claude_sdk", tin=1000)])]
    )
    row = _by_provider(report)["claude_sdk"]
    assert row["prompt_tokens"] == 2000
    assert row["cached_prompt_tokens"] == 800
    assert row["cached_prefix_pct"] == 40.0


def test_the_cost_per_iteration_is_per_model_call_not_per_run() -> None:
    # Es la unidad que decide si merece la pena tocar la construcción de
    # mensajes: lo que cuesta UN turno más de contexto.
    report = build_prompt_cache_report(
        [(None, [_call("ollama", tin=500, cost=0.02), _call("ollama", tin=500, cost=0.04)])]
    )
    assert _by_provider(report)["ollama"]["cost_per_iteration_usd"] == 0.03


def test_a_provider_that_never_reports_cache_says_so() -> None:
    # ÉSTE es el test que importa. Sin `reports_cache`, un 0 % se leería como
    # «no hay reutilización» cuando puede ser «no lo dice» — y se acabaría
    # optimizando contra un dato que no existe.
    report = build_prompt_cache_report([(None, [_call("ollama", tin=1000)])])
    row = _by_provider(report)["ollama"]
    assert row["cached_prefix_pct"] == 0.0
    assert row["reports_cache"] is False


def test_a_provider_that_reports_zero_is_distinguishable() -> None:
    report = build_prompt_cache_report(
        [(None, [_call("azure_foundry", tin=1000, cached=1), _call("azure_foundry", tin=1000)])]
    )
    row = _by_provider(report)["azure_foundry"]
    assert row["reports_cache"] is True
    assert row["calls_reporting_cache"] == 1


def test_each_provider_is_measured_separately() -> None:
    # La pregunta de la tarea es «por cada uno de los cuatro proveedores»: un
    # promedio global escondería que uno cachea y otro no.
    report = build_prompt_cache_report(
        [
            (None, [_call("claude_sdk", tin=1000, cached=900)]),
            (None, [_call("ollama", tin=1000)]),
        ]
    )
    rows = _by_provider(report)
    assert rows["claude_sdk"]["cached_prefix_pct"] == 90.0
    assert rows["ollama"]["cached_prefix_pct"] == 0.0


def test_runs_are_counted_once_per_provider() -> None:
    report = build_prompt_cache_report(
        [(None, [_call("claude_sdk", tin=10), _call("claude_sdk", tin=10)])]
    )
    row = _by_provider(report)["claude_sdk"]
    assert row["runs"] == 1
    assert row["model_calls"] == 2


def test_non_model_steps_are_ignored() -> None:
    report = build_prompt_cache_report(
        [(None, [{"kind": "tool_call", "tool": "read_file"}, _call("claude_sdk", tin=10)])]
    )
    assert _by_provider(report)["claude_sdk"]["model_calls"] == 1


def test_a_step_without_provider_falls_back_to_the_run_hint() -> None:
    step = _call("", tin=10)
    step.pop("provider")
    report = build_prompt_cache_report([("copilot", [step])])
    assert "copilot" in _by_provider(report)


def test_a_step_with_neither_is_labelled_not_hidden() -> None:
    # Descartarlo silenciosamente falsearía los totales.
    step = _call("", tin=10)
    step.pop("provider")
    report = build_prompt_cache_report([(None, [step])])
    assert "desconocido" in _by_provider(report)


def test_no_runs_is_an_empty_report_not_a_crash() -> None:
    assert build_prompt_cache_report([]).as_dict() == {"providers": [], "total_model_calls": 0}
