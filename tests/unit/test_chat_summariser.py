"""El `Summariser` de producción del chat (task_wf_06 b).

`db/conversation_compression.py` define el `Summariser` como un `Protocol` para
quedarse libre de SDK de LLM, y su docstring lleva desde el Plan 03 diciendo que
«el bucle de agente enchufará uno real respaldado por `shared_llm.LLMProvider`».
Nunca se enchufó: el único implementador era el `ScriptedSummariser` de los tests.
Este módulo es ese implementador.

Dos cosas se fijan aquí:

* **La doble representación.** El LLM devuelve un objeto con prosa y cuatro listas
  (requisitos / decisiones / descartado / abierto). La prosa va a `content` para el
  humano; las listas van al `summary_record` que el pliegue copia literal.
* **El modo de fallo explícito**, con la forma de `DistillationResult`
  (`memorizer/distillation.py:127-141`): un discriminante `cause` con
  `ok | llm_empty | llm_unparseable | llm_error`. Ese diseño nació justamente
  porque conflatar los tres hacía indiagnosticable el fallo. Y nunca lanza hacia
  el turno: sin resumen, la conversación sigue sin comprimir.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from api_server.chat.summariser import LLMSummariser
from api_server.db.conversation_compression import SummaryRecord
from shared_llm.types import CompletionResponse, Message, StreamChunk, Usage

pytestmark = pytest.mark.unit


class FakeLLM:
    """`LLMProvider`-shaped fake: devuelve un `content` fijo o levanta."""

    name = "fake"

    def __init__(self, *, content: str = "{}", raises: Exception | None = None) -> None:
        self.content = content
        self.raises = raises
        self.last_messages: list[Message] = []
        self.calls = 0

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        self.calls += 1
        self.last_messages = list(messages)
        if self.raises is not None:
            raise self.raises
        return CompletionResponse(
            content=self.content,
            model=str(kwargs.get("model") or "fake-model"),
            provider=self.name,
            usage=Usage(),
            tool_calls=None,
            raw={},
        )

    def stream(self, messages: Sequence[Message], **kwargs: Any) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None

    def prompt_text(self) -> str:
        return "\n".join(m.content for m in self.last_messages)


def _msg(content: str, *, author_kind: str = "user") -> Any:
    return SimpleNamespace(content=content, author_kind=author_kind, attachments=[])


_GOOD_PAYLOAD = {
    "resumen": "El usuario pide un lector de PDFs offline; el equipo elige SQLite.",
    "requisitos": ["debe funcionar sin conexión"],
    "decisiones": ["usamos SQLite"],
    "descartado": ["nada de Electron"],
    "abierto": ["¿quién paga el certificado?"],
}


# ---------------------------------------------------------------------------
# Camino feliz: prosa + registro
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_returns_prose_for_the_human_and_a_record_for_the_fold() -> None:
    llm = FakeLLM(content=json.dumps(_GOOD_PAYLOAD, ensure_ascii=False))
    result = await LLMSummariser(provider=llm).summarise_window([_msg("hola")])

    assert result.cause == "ok"
    assert result.ok is True
    assert "lector de PDFs offline" in result.content
    assert result.record == SummaryRecord(
        requisitos=("debe funcionar sin conexión",),
        decisiones=("usamos SQLite",),
        descartado=("nada de Electron",),
        abierto=("¿quién paga el certificado?",),
    )


@pytest.mark.asyncio
async def test_json_wrapped_in_prose_or_fences_still_parses() -> None:
    """Los modelos pequeños envuelven el JSON en explicaciones o en ```json."""
    llm = FakeLLM(content=f"Claro, aquí tienes:\n```json\n{json.dumps(_GOOD_PAYLOAD)}\n```\n")
    result = await LLMSummariser(provider=llm).summarise_window([_msg("hola")])
    assert result.cause == "ok"
    assert result.record.requisitos == ("debe funcionar sin conexión",)


@pytest.mark.asyncio
async def test_a_record_entry_that_is_not_a_string_is_dropped() -> None:
    llm = FakeLLM(
        content=json.dumps({"resumen": "x", "requisitos": ["R1", {"a": 1}, None, "  ", "R2"]})
    )
    result = await LLMSummariser(provider=llm).summarise_window([_msg("hola")])
    assert result.record.requisitos == ("R1", "R2")


# ---------------------------------------------------------------------------
# Los cuatro valores de `cause`
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cause_llm_error_when_the_call_raises() -> None:
    llm = FakeLLM(raises=RuntimeError("provider caído"))
    result = await LLMSummariser(provider=llm).summarise_window([_msg("hola")])
    assert result.cause == "llm_error"
    assert result.ok is False


@pytest.mark.asyncio
async def test_cause_llm_unparseable_when_the_answer_is_not_json() -> None:
    llm = FakeLLM(content="pues mira, no me apetece devolver JSON")
    result = await LLMSummariser(provider=llm).summarise_window([_msg("hola")])
    assert result.cause == "llm_unparseable"
    assert result.ok is False


@pytest.mark.asyncio
async def test_cause_llm_empty_when_the_model_returns_nothing_useful() -> None:
    """JSON válido pero sin prosa ni entradas: el modelo respondió, no hay nada
    que guardar. Es la única causa legítima y por eso se distingue."""
    llm = FakeLLM(content=json.dumps({"resumen": "", "requisitos": []}))
    result = await LLMSummariser(provider=llm).summarise_window([_msg("hola")])
    assert result.cause == "llm_empty"
    assert result.ok is False


@pytest.mark.asyncio
async def test_a_record_without_prose_is_still_usable() -> None:
    """Si el modelo se deja la prosa pero acierta el registro, comprimir sigue
    siendo mejor que no comprimir: el registro es lo que hay que conservar."""
    llm = FakeLLM(content=json.dumps({"resumen": "", "requisitos": ["R1"]}))
    result = await LLMSummariser(provider=llm).summarise_window([_msg("hola")])
    assert result.cause == "ok"
    assert result.record.requisitos == ("R1",)
    assert result.content.strip() != ""


@pytest.mark.asyncio
async def test_a_timeout_is_an_llm_error_not_an_exception_towards_the_turn() -> None:
    """Un fallo del resumen no puede tumbar la respuesta del equipo. `TimeoutError`
    se prueba aparte porque el responder la captura arriba con su propio `except`."""
    result = await LLMSummariser(provider=FakeLLM(raises=TimeoutError())).summarise_window(
        [_msg("hola")]
    )
    assert result.cause == "llm_error"


# ---------------------------------------------------------------------------
# El prompt
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_prompt_carries_the_window_verbatim_with_its_speakers() -> None:
    llm = FakeLLM(content=json.dumps(_GOOD_PAYLOAD))
    await LLMSummariser(provider=llm).summarise_window(
        [_msg("quiero un lector de PDFs"), _msg("propongo SQLite", author_kind="agent")]
    )
    prompt = llm.prompt_text()
    assert "quiero un lector de PDFs" in prompt
    assert "propongo SQLite" in prompt


@pytest.mark.asyncio
async def test_an_empty_window_does_not_call_the_model() -> None:
    """Cuando la ventana solo pliega resúmenes ya estructurados no hay nada crudo
    que resumir: la fusión es determinista y gastar una llamada sería tirar dinero."""
    llm = FakeLLM(content=json.dumps(_GOOD_PAYLOAD))
    result = await LLMSummariser(provider=llm).summarise_window([])
    assert llm.calls == 0
    assert result.cause == "llm_empty"


@pytest.mark.asyncio
async def test_the_model_is_the_one_the_chat_turn_already_resolved() -> None:
    """Decisión de diseño: no se añade un eje de configuración nuevo para el
    resumen. `chat_model_config` ya es la palanca del operador."""

    class Recording(FakeLLM):
        def __init__(self) -> None:
            super().__init__(content=json.dumps(_GOOD_PAYLOAD))
            self.model_seen: str | None = None

        async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
            self.model_seen = kwargs.get("model")
            return await super().complete(messages, **kwargs)

    llm = Recording()
    await LLMSummariser(provider=llm, model="gpt-4o-mini").summarise_window([_msg("hola")])
    assert llm.model_seen == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Compatibilidad con el `Protocol` original
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_still_satisfies_the_original_summariser_protocol() -> None:
    """`compress_old_messages` acepta cualquier `Summariser`; el de producción
    debe seguir sirviendo por la vía antigua (prosa a secas)."""
    from api_server.db.conversation_compression import Summariser

    summariser = LLMSummariser(provider=FakeLLM(content=json.dumps(_GOOD_PAYLOAD)))
    assert isinstance(summariser, Summariser)
    text = await summariser.summarise([_msg("hola")])
    assert "lector de PDFs offline" in text
