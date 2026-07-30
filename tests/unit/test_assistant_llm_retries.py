"""El asistente reintenta lo transitorio (prod-07 task_prod07_01, llm-2).

Antes de esto el asistente NO tenía retry en ningún punto: un 429 del
proveedor —o un socket cortado— llegaba tal cual al router y el turno del
usuario moría con un error. El agent-runtime sí reintentaba (F25/F30); el
asistente conservaba el patrón roto.

Lo que estos tests fijan, y por qué cada uno importa:

* un 429 puntual NO mata el turno (el modo de fallo del hallazgo);
* un ``AuthError`` NO se reintenta — un token revocado no se arregla
  preguntando otra vez, y reintentar retrasa el error que el operador
  necesita ver;
* cada reintento se REGISTRA con provider/intento/causa: un retry silencioso
  esconde a la vez un proveedor inestable y el gasto duplicado de tokens;
* ``decide_stream`` NO se reintenta: los deltas ya emitidos se repetirían en
  la pantalla del usuario.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from api_server.assistant.graph import AssistantState
from api_server.assistant.llm import LLMAssistantModel
from shared_llm import Message
from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.types import CompletionResponse, StreamChunk, Usage

pytestmark = pytest.mark.unit


class _FlakyProvider:
    """Falla las primeras ``fail_times`` llamadas con ``exc``, luego responde."""

    name = "ollama"

    def __init__(self, *, exc: BaseException, fail_times: int) -> None:
        self._exc = exc
        self._fail_times = fail_times
        self.calls = 0
        self.stream_calls = 0

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        return CompletionResponse(
            content="respuesta", model="m", provider="ollama", usage=Usage(3, 4, 0.0)
        )

    async def stream(
        self, messages: Sequence[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        self.stream_calls += 1
        if self.stream_calls <= self._fail_times:
            raise self._exc
        yield StreamChunk(delta="hola")
        yield StreamChunk(delta="", done=True, usage=Usage(1, 1, 0.0))

    async def aclose(self) -> None:  # pragma: no cover — no lo ejercita este test
        return None


def _state() -> AssistantState:
    return AssistantState(system_prompt="s", chat_history=[], enabled_tools=())


def _model(provider: Any, **kwargs: Any) -> LLMAssistantModel:
    # sleep inyectado: el test no espera de verdad al backoff.
    return LLMAssistantModel(provider=provider, model="m", retry_sleep=_no_sleep, **kwargs)


async def _no_sleep(_delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_decide_survives_a_transient_rate_limit() -> None:
    provider = _FlakyProvider(exc=RateLimitError("429"), fail_times=1)
    turn = await _model(provider).decide(_state())
    assert turn.content == "respuesta"
    assert provider.calls == 2  # reintentó una vez y salió adelante


@pytest.mark.asyncio
async def test_decide_survives_a_dropped_connection() -> None:
    """El corte de red llega como ProviderError SIN status (lo marca
    ``typed_transport_errors``); tiene que reintentarse igual que el 429."""
    provider = _FlakyProvider(exc=ProviderError("transport error", transient=True), fail_times=1)
    turn = await _model(provider).decide(_state())
    assert turn.content == "respuesta"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_decide_does_not_retry_auth_error() -> None:
    provider = _FlakyProvider(exc=AuthError("token revocado"), fail_times=99)
    with pytest.raises(AuthError):
        await _model(provider).decide(_state())
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_decide_does_not_retry_a_4xx() -> None:
    provider = _FlakyProvider(exc=ProviderError("bad request", status_code=400), fail_times=99)
    with pytest.raises(ProviderError):
        await _model(provider).decide(_state())
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_decide_gives_up_and_reraises_the_typed_error() -> None:
    provider = _FlakyProvider(exc=RateLimitError("429"), fail_times=99)
    with pytest.raises(RateLimitError):
        await _model(provider, retry_attempts=2).decide(_state())
    assert provider.calls == 2  # presupuesto respetado, ni uno más


@pytest.mark.asyncio
async def test_retry_attempts_1_disables_retries() -> None:
    """Escotilla de escape: un despliegue puede volver al comportamiento previo."""
    provider = _FlakyProvider(exc=RateLimitError("429"), fail_times=99)
    with pytest.raises(RateLimitError):
        await _model(provider, retry_attempts=1).decide(_state())
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_each_retry_is_logged_with_provider_attempt_and_cause() -> None:
    """Un reintento invisible esconde el proveedor inestable Y el gasto doble."""
    provider = _FlakyProvider(exc=RateLimitError("429"), fail_times=1)
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("api_server.assistant.llm")
    handler = _Sink()
    logger.addHandler(handler)
    previous, logger.disabled = logger.disabled, False
    try:
        await _model(provider).decide(_state())
    finally:
        logger.removeHandler(handler)
        logger.disabled = previous

    assert len(records) == 1, f"esperaba 1 línea de reintento, hubo {len(records)}"
    record = records[0]
    assert record.levelno == logging.WARNING
    assert getattr(record, "llm_provider", None) == "ollama"
    assert getattr(record, "llm_retry_attempt", None) == 1
    assert getattr(record, "llm_retry_cause", None) == "RateLimitError"


@pytest.mark.asyncio
async def test_decide_stream_is_not_retried() -> None:
    """Reintentar un stream reemitiría los deltas ya vistos por el usuario."""
    provider = _FlakyProvider(exc=RateLimitError("429"), fail_times=1)
    with pytest.raises(RateLimitError):
        async for _delta in _model(provider).decide_stream(_state()):
            pass
    assert provider.stream_calls == 1
