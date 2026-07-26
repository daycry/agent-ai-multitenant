"""El latido que muestrea shadow evals (`task_wf_52b`).

`record_shadow_eval` existía desde el Plan 14 con su muestreador determinista,
su tabla y su dashboard… y **ningún llamante**. El mecanismo entero, nunca
disparado.

Lo que se prueba aquí son las guardas que deciden si el latido gasta dinero.
Cada corrida en la sombra son N llamadas de juez, así que un beat que se
enciende solo al instalar sería una factura sorpresa. Las tres condiciones
—tasa, juez nombrado, dataset `shadow`— tienen que fallar cerradas.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from workers.maintenance.shadow_evals import (
    CANDIDATE_WINDOW_HOURS,
    JUDGE_MODEL_ENV_VAR,
    MAX_SHADOW_EVALS_PER_BEAT,
    _run_shadow_evals_async,
    _subject_model_of,
)


class _Settings:
    database_url = "postgresql+asyncpg://nadie@127.0.0.1:1/nada"


# ---------------------------------------------------------------------------
# El grifo cerrado por defecto
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_without_a_named_judge_it_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sin saber quién juzga no se puede juzgar — y encima la guarda
    # anti-auto-aprobado compara nombres. Sale ANTES de abrir la BD: el DSN de
    # `_Settings` no existe, así que si tocase la base este test fallaría.
    monkeypatch.delenv(JUDGE_MODEL_ENV_VAR, raising=False)
    out = await _run_shadow_evals_async(_Settings())  # type: ignore[arg-type]
    assert out == {"status": "off", "reason": "no_judge_model", "sampled": 0}


@pytest.mark.asyncio
async def test_a_blank_judge_name_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Una variable puesta a espacios es el descuido típico de un .env; tratarla
    # como «juez llamado ‹espacio›» acabaría en 409 por cada tarea.
    monkeypatch.setenv(JUDGE_MODEL_ENV_VAR, "   ")
    out = await _run_shadow_evals_async(_Settings())  # type: ignore[arg-type]
    assert out["reason"] == "no_judge_model"


@pytest.mark.asyncio
async def test_rate_zero_stops_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # El operador tiene que poder cerrar el grifo del todo sin desplegar nada.
    monkeypatch.setenv(JUDGE_MODEL_ENV_VAR, "juez")
    monkeypatch.setenv("EVAL_SHADOW_SAMPLE_RATE", "0")
    out = await _run_shadow_evals_async(_Settings())  # type: ignore[arg-type]
    assert out == {"status": "off", "reason": "rate_zero", "sampled": 0}


@pytest.mark.asyncio
async def test_a_malformed_rate_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Un `EVAL_SHADOW_SAMPLE_RATE=cinco por ciento` no puede convertirse en «al
    # 100 %»: fallar abierto aquí es un gasto que nadie pidió.
    monkeypatch.setenv(JUDGE_MODEL_ENV_VAR, "juez")
    monkeypatch.setenv("EVAL_SHADOW_SAMPLE_RATE", "cinco por ciento")
    out = await _run_shadow_evals_async(_Settings())  # type: ignore[arg-type]
    assert out["status"] == "off"
    assert out["sampled"] == 0


# ---------------------------------------------------------------------------
# De qué modelo se dice que es la salida juzgada
# ---------------------------------------------------------------------------
def test_the_subject_model_comes_from_the_last_model_call() -> None:
    # Es el que produjo el entregable que se está juzgando; atribuirlo al
    # primero mentiría en la comparación entre releases.
    steps = [
        {"kind": "model_call", "model": "viejo"},
        {"kind": "tool_call", "tool": "read_file"},
        {"kind": "model_call", "model": "nuevo"},
    ]
    assert _subject_model_of(steps) == "nuevo"


def test_a_run_with_no_model_call_has_no_subject_model() -> None:
    assert _subject_model_of([{"kind": "tool_call"}]) is None
    assert _subject_model_of([]) is None
    assert _subject_model_of(None) is None


def test_steps_that_are_not_dicts_do_not_break_it() -> None:
    # `steps_log` es JSONB: nada garantiza su forma en filas viejas, y una
    # excepción aquí tumbaría el latido entero por una fila rara.
    assert _subject_model_of(["basura", None, {"kind": "model_call", "model": "m"}]) == "m"


def test_an_empty_model_name_is_not_a_model() -> None:
    assert _subject_model_of([{"kind": "model_call", "model": "  "}]) is None


# ---------------------------------------------------------------------------
# Los topes que impiden que un pico de tareas sea un pico de factura
# ---------------------------------------------------------------------------
def test_the_beat_is_bounded_per_run() -> None:
    assert 0 < MAX_SHADOW_EVALS_PER_BEAT <= 20


def test_the_candidate_window_is_recent() -> None:
    # Al encender la feature, el primer latido no puede intentar muestrear el
    # histórico entero.
    assert 0 < CANDIDATE_WINDOW_HOURS <= 72


def test_the_documented_default_rate_is_a_small_sample() -> None:
    from api_server.evals.constants import DEFAULT_SHADOW_SAMPLE_RATE

    assert Decimal("0") < DEFAULT_SHADOW_SAMPLE_RATE <= Decimal("0.1")
