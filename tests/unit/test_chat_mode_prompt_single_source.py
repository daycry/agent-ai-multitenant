"""La pantalla de «prompt efectivo» enseñaba un prompt que el chat no envía.

El defecto: había DOS catálogos de prompts de modo.

* `api_server.chat.modes` — lo que publica `GET /chat-modes` y lo que la sección
  Persona pinta como «prompt efectivo» del rol + modo;
* `api_server.chat.responder._MODE_PROMPTS` — lo que `_simple_reply` metía de
  verdad como mensaje `system` en la llamada al LLM.

Los textos eran distintos, así que la pantalla de auditoría mostraba algo que no
se enviaba. Nadie mentía a propósito: simplemente nada ataba las dos mitades, y
por eso se ata aquí.

Lo que este fichero exige:

* el `system` que sale hacia el proveedor en discussion y execution es
  EXACTAMENTE el que publica el catálogo (identidad de objeto, no «se parece»);
* `_MODE_PROMPTS` ya no existe: un segundo catálogo vuelve a abrir la brecha, así
  que reaparecer rompe la suite;
* la fusión conservó lo que el texto de `responder` aportaba y que el de `modes`
  no tenía (una sola voz, markdown), y quitó lo que era falso en este canal
  (instrucciones de llamar a tools que `_simple_reply` no entrega);
* `planning` no pasa por aquí: su prompt lo compone `planning_llm.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from api_server.chat import responder
from api_server.chat.modes import BUILTIN_MODES, BuiltinChatMode, list_chat_modes
from shared_llm.types import CompletionResponse, Message, StreamChunk

pytestmark = pytest.mark.unit


class _CapturingProvider:
    """Un `LLMProvider` que no llama a nadie: sólo guarda lo que se le mandó."""

    name = "capturing"

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        self.messages = list(messages)
        return CompletionResponse(content="respuesta", model=model or "", provider=self.name)

    def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def _system_sent_for(mode: str) -> str:
    """El contenido del mensaje `system` que `_simple_reply` manda de verdad."""
    provider = _CapturingProvider()
    asyncio.run(
        responder._simple_reply(
            provider,
            "modelo-de-prueba",
            mode,
            [{"role": "user", "content": "hola"}],
            0.7,
            {},
        )
    )
    assert provider.messages, "el provider no recibió ningún mensaje"
    first = provider.messages[0]
    assert first.role == "system"
    return str(first.content)


def _published_prompt(mode: str) -> str:
    """El prompt que la UI enseña como «efectivo» (GET /chat-modes)."""
    listing = next(m for m in list_chat_modes() if m.name == mode)
    return listing.system_prompt


# ---------------------------------------------------------------------------
# Una sola fuente: lo que se enseña es lo que se envía
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["discussion", "execution"])
def test_the_prompt_sent_is_the_prompt_the_catalog_publishes(mode: str) -> None:
    sent = _system_sent_for(mode)
    assert sent == _published_prompt(mode)
    # Identidad, no igualdad: si algún día alguien re-teclea el texto en
    # `responder` volveríamos al mismo sitio con dos copias que HOY coinciden.
    assert sent is BUILTIN_MODES[mode].system_prompt


def test_discussion_and_execution_do_not_share_a_prompt() -> None:
    """Guarda contra la unificación perezosa: fundir los dos catálogos no puede
    resolverse mandando el mismo texto en los dos modos."""
    assert _system_sent_for("discussion") != _system_sent_for("execution")


def test_a_second_mode_prompt_catalog_no_longer_exists() -> None:
    """El defecto no fue un texto desactualizado: fue que hubiera DOS sitios.

    Mientras `responder` tenga su propio diccionario de prompts, el siguiente que
    edite uno de los dos reabre la brecha sin enterarse."""
    assert not hasattr(responder, "_MODE_PROMPTS"), (
        "ha vuelto a aparecer un catálogo de prompts de modo en `responder`: "
        "el único es `api_server.chat.modes.BUILTIN_MODES`"
    )


def test_an_unknown_mode_still_falls_back_to_discussion() -> None:
    """No-regresión: hoy `_MODE_PROMPTS.get(mode, ...["discussion"])` servía
    discussion para cualquier modo desconocido (incluido `custom`, que llega aquí
    sin registro de tenant). Ese comportamiento no cambia."""
    assert _system_sent_for("custom") == _published_prompt("discussion")
    assert _system_sent_for("no-existe") == _published_prompt("discussion")


def test_planning_never_gets_its_prompt_from_here() -> None:
    """`planning` va por el sub-grafo (`_stream_planning`) y su prompt se compone
    en `planning_llm.py`. Si `_simple_reply` sirviese el texto de planning del
    catálogo, quien auditase creería que ESE es el prompt de planning que se
    envía — la misma confusión que este fichero cierra, una planta más abajo."""
    assert _system_sent_for("planning") != _published_prompt("planning")
    assert _system_sent_for("planning") == _published_prompt("discussion")


# ---------------------------------------------------------------------------
# Qué sobrevivió a la fusión (y qué no)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["discussion", "execution"])
def test_the_fused_prompt_keeps_the_single_voice_and_markdown(mode: str) -> None:
    """Lo que aportaba el texto de `responder` y el de `modes` no tenía.

    `_simple_reply` es UNA llamada al LLM que produce UN mensaje firmado por el
    equipo. El texto de `modes` decía «cada agente puede intervenir libremente»,
    que en este canal es falso y empuja al modelo a un diálogo fingido. Y el
    frontend renderiza markdown, así que pedirlo no es cosmética."""
    prompt = BUILTIN_MODES[mode].system_prompt.lower()
    assert "portavoz" in prompt
    assert "markdown" in prompt


def test_the_execution_prompt_does_not_promise_tools_this_channel_lacks() -> None:
    """El modo `execution` del chat NO ejecuta nada.

    `_simple_reply` no entrega tools: bifurcar hacia el sub-grafo sólo ocurre en
    `planning`, y la ejecución real la arranca `POST /plans/{id}/start-execution`.
    El texto original de `modes` ordenaba «registra avances en task_comment,
    actualiza estados vía kanban_update» — instrucciones que el modelo no puede
    cumplir, y cuyo incumplimiento se lee como que el equipo no hizo su trabajo.
    Se retiraron al fundir; que no vuelvan sin cablear las tools (lo cual pide
    ADR: encender una restricción decorativa cambia el comportamiento de todos
    los agentes de golpe)."""
    prompt = BUILTIN_MODES["execution"].system_prompt
    for tool in ("task_comment", "kanban_update"):
        assert tool not in prompt, (
            f"el prompt de execution ordena usar `{tool}`, pero `_simple_reply` "
            f"no entrega ninguna tool en este canal"
        )


def test_the_execution_prompt_still_names_the_human_approval_engine() -> None:
    """Lo que sí aportaba el texto de `modes` y había que conservar: que las
    acciones sensibles pasan por el motor de aprobación humana del proyecto
    (principio 11 del CLAUDE.md)."""
    prompt = BUILTIN_MODES["execution"].system_prompt.lower()
    assert "aprobación humana" in prompt


def test_the_discussion_prompt_still_refuses_to_produce_a_plan() -> None:
    """El matiz que los dos textos compartían y que ordena el modo: en discussion
    NO se produce un plan estructurado (para eso está `planning`)."""
    prompt = BUILTIN_MODES[BuiltinChatMode.DISCUSSION.value].system_prompt.lower()
    assert "plan estructurado" in prompt
