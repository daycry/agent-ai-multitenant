"""El runtime reintenta lo transitorio con la política ÚNICA (prod-07 task_prod07_01).

El runtime ya tenía retry (F25/F30) pero con su propia clasificación, y le
faltaban tres cosas que el hallazgo llm-2 señala:

  1. **Un corte de red no se reintentaba.** ``typed_transport_errors`` convierte
     un socket reseteado en ``ProviderError`` SIN ``status_code``, y la regla
     local ("5xx → transitorio") lo archivaba como permanente. El blip de red
     mataba el run — que es literalmente el modo de fallo del hallazgo.
  2. **No se respetaba ``Retry-After``.** Volver antes de que la ventana del
     proveedor reabra garantiza otro 429 y quema un intento.
  3. **Los reintentos no se registraban.** No había forma de saber, leyendo el
     log de un run, que el proveedor había fallado y que se habían pagado los
     tokens del prompt dos veces.

Además fija el JITTER: sin él, N agentes en paralelo que topan el mismo
rate-limit vuelven todos en el mismo instante.

``test_provider_robustness.py`` cubre lo que ya funcionaba (timeout tipado,
4xx/AuthError permanentes, re-raise al agotar); esto cubre lo que no.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import pytest
from agent_runtime.providers import (
    ProviderTimeout,
    _is_transient,
    _ProviderModelClient,
    _run_with_retry,
)
from shared_llm import AuthError, Message, ProviderError, RateLimitError
from shared_llm.types import CompletionResponse, Usage


# ---------------------------------------------------------------------------
# 1. Clasificación — el hueco real: el error de transporte
# ---------------------------------------------------------------------------
def test_transport_error_without_status_is_transient() -> None:
    """El fallo que mataba runs: ProviderError(transient=True) sin status."""
    assert _is_transient(ProviderError("ollama: transport error — reset", transient=True)) is True


def test_malformed_body_is_not_transient() -> None:
    """Un 200 con cuerpo roto NO se reintenta: el gateway lo romperá igual y
    pagaríamos dos veces por la misma basura."""
    assert _is_transient(ProviderError("respuesta sin choices", raw={"x": 1})) is False


def test_runtime_timeout_stays_transient() -> None:
    """No-regresión: ``ProviderTimeout`` es local del runtime (subclase de
    LLMError, NO de TimeoutError), así que la delegación a shared_llm tiene que
    seguir contemplándolo explícitamente."""
    assert _is_transient(ProviderTimeout("900s")) is True


def test_auth_and_4xx_remain_permanent() -> None:
    assert _is_transient(AuthError("revoked")) is False
    assert _is_transient(ProviderError("bad request", status_code=400)) is False


def test_5xx_remains_transient() -> None:
    assert _is_transient(ProviderError("upstream", status_code=503)) is True


# ---------------------------------------------------------------------------
# 2. Backoff: jitter y Retry-After
# ---------------------------------------------------------------------------
def test_backoff_applies_jitter() -> None:
    """Con el jitter en su mínimo la espera es la MITAD del backoff nominal.
    Esa dispersión es lo que evita que N agentes vuelvan en tromba."""
    slept: list[float] = []

    async def _always_rl() -> str:
        raise RateLimitError("429")

    with pytest.raises(RateLimitError):
        _run_with_retry(_always_rl, attempts=3, backoff=1.0, sleep=slept.append, jitter=lambda: 0.0)
    assert slept == [0.5, 1.0]


def test_backoff_honours_retry_after_from_the_provider() -> None:
    """El proveedor dijo 7s: se esperan 7s, no el backoff que adivinamos."""
    slept: list[float] = []

    async def _rate_limited() -> str:
        raise RateLimitError("429", retry_after=7.0)

    with pytest.raises(RateLimitError):
        _run_with_retry(_rate_limited, attempts=2, backoff=1.0, sleep=slept.append)
    assert slept == [7.0]


def test_transport_error_is_actually_retried_end_to_end() -> None:
    """El test que faltaba: el corte de red se reintenta y el run sobrevive."""
    calls: list[int] = []

    async def _flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise ProviderError("transport error — connection reset", transient=True)
        return "ok"

    assert _run_with_retry(_flaky, attempts=3, backoff=0.0) == "ok"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 3. Observabilidad — provider / intento / causa
# ---------------------------------------------------------------------------
def test_each_retry_is_logged_with_provider_attempt_and_cause() -> None:
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    async def _flaky() -> str:
        if len(records) == 0:
            raise RateLimitError("429")
        return "ok"

    logger = logging.getLogger("agent_runtime.providers")
    handler = _Sink()
    logger.addHandler(handler)
    previous, logger.disabled = logger.disabled, False
    try:
        assert _run_with_retry(_flaky, attempts=3, backoff=0.0, provider="ollama") == "ok"
    finally:
        logger.removeHandler(handler)
        logger.disabled = previous

    assert len(records) == 1, f"esperaba 1 línea de reintento, hubo {len(records)}"
    assert records[0].levelno == logging.WARNING
    assert getattr(records[0], "llm_provider", None) == "ollama"
    assert getattr(records[0], "llm_retry_attempt", None) == 1
    assert getattr(records[0], "llm_retry_cause", None) == "RateLimitError"


# ---------------------------------------------------------------------------
# 4. Cableado: `decide()` del adaptador real pasa por el retry
# ---------------------------------------------------------------------------
class _FlakyProvider:
    """Falla la primera ``complete()`` con ``exc``; luego responde FINISH."""

    name = "ollama"

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def complete(self, _messages: Sequence[Message], **_kwargs: Any) -> CompletionResponse:
        self.calls += 1
        if self.calls == 1:
            raise self._exc
        return CompletionResponse(
            content="listo", model="m", provider="ollama", usage=Usage(1, 2, 0.0)
        )

    async def aclose(self) -> None:  # pragma: no cover
        return None


@pytest.mark.parametrize(
    "exc",
    [
        RateLimitError("429"),
        ProviderError("transport error — reset", transient=True),
        ProviderError("upstream", status_code=503),
    ],
)
def test_decide_survives_a_transient_failure(
    exc: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El punto 5 de `verificar-antes-de-implementar`: el mecanismo no está hecho
    si nadie lo llama. Esto ejercita `decide()` del adaptador REAL, no el helper."""
    monkeypatch.setattr("agent_runtime.providers._DEFAULT_RETRY_BACKOFF_S", 0.0)
    provider = _FlakyProvider(exc)
    client = _ProviderModelClient(provider=provider, model="m")
    decision = client.decide({"task": {"title": "haz algo"}, "context": []})
    assert provider.calls == 2
    assert decision is not None


def test_decide_does_not_retry_an_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_runtime.providers._DEFAULT_RETRY_BACKOFF_S", 0.0)
    provider = _FlakyProvider(AuthError("token revocado"))
    client = _ProviderModelClient(provider=provider, model="m")
    with pytest.raises(AuthError):
        client.decide({"task": {"title": "haz algo"}, "context": []})
    assert provider.calls == 1


# ---------------------------------------------------------------------------
# `task_cv_40` (auditoría 2026-09-01, D-05): el wall-clock sólo se miraba entre
# iteraciones; una llamada podía rebasarlo 45 min. El cliente conoce el restante
# y lo usa como `timeout` de la llamada.
# ---------------------------------------------------------------------------


class _QuietProvider:
    name = "ollama"

    async def complete(self, _messages: Sequence[Message], **_kwargs: Any) -> CompletionResponse:
        return CompletionResponse(
            content="listo", model="m", provider="ollama", usage=Usage(1, 2, 0.0)
        )


def _capture_timeouts(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    import agent_runtime.providers as providers_mod

    seen: list[float] = []
    original = providers_mod._run_with_retry

    def _spy(make_coro: Any, **kwargs: Any) -> Any:
        seen.append(float(kwargs.get("timeout", providers_mod._DEFAULT_CALL_TIMEOUT_S)))
        return original(make_coro, **kwargs)

    monkeypatch.setattr(providers_mod, "_run_with_retry", _spy)
    return seen


def test_the_call_timeout_never_exceeds_the_remaining_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture_timeouts(monkeypatch)
    client = _ProviderModelClient(provider=_QuietProvider(), model="m")
    client.bind_deadline(lambda: 7.5)

    client.decide({"task": {"title": "haz algo"}, "context": []})

    assert seen == [7.5]


def test_without_a_deadline_the_default_timeout_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_runtime.providers as providers_mod

    seen = _capture_timeouts(monkeypatch)
    client = _ProviderModelClient(provider=_QuietProvider(), model="m")

    client.decide({"task": {"title": "haz algo"}, "context": []})

    assert seen == [float(providers_mod._DEFAULT_CALL_TIMEOUT_S)]


def test_an_exhausted_wall_clock_still_leaves_a_floor_for_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restante ≤ 0: el tracker abortará en el siguiente check; la llamada en
    curso no se lanza con `timeout=0` (que reventaría con un error confuso)."""
    import agent_runtime.providers as providers_mod

    seen = _capture_timeouts(monkeypatch)
    client = _ProviderModelClient(provider=_QuietProvider(), model="m")
    client.bind_deadline(lambda: -12.0)

    client.decide({"task": {"title": "haz algo"}, "context": []})

    assert seen == [float(providers_mod._MIN_CALL_TIMEOUT_S)]
